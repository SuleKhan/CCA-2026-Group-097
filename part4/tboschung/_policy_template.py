from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.client.rest import ApiException
import subprocess
import time
import logging
import os
from datetime import datetime

from _scheduler_logger import SchedulerLogger, Job

# =========================
# CONFIG
# =========================

CHECK_INTERVAL = 5
TOTAL_CORES    = [0, 1, 2, 3]
THREADS        = 8
NAMESPACE      = "default"

# The Kubernetes node name that batch jobs should be scheduled on.
# Run `kubectl get nodes` to find yours.
BATCH_NODE     = "batch-node"

# SSH config — for memcached control only (memcached runs as a system
# service, not a pod, so taskset still goes over SSH)
SSH_KEY        = "~/.ssh/cloud-computing"
SSH_OPTS       = "-o StrictHostKeyChecking=no"
MEMCACHED_IP   = "34.78.123.59"

# =========================
# LOGGING SETUP
# =========================

def setup_logging(log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"scheduler_{timestamp}.log")

    log = logging.getLogger("scheduler")
    log.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    log.addHandler(fh)
    log.addHandler(ch)
    log.info(f"Logging to: {log_file}")
    return log

log = setup_logging()

# =========================
# KUBERNETES CLIENT
#
# Loads from ~/.kube/config automatically (same cluster your kubectl
# talks to). No extra setup needed beyond a working kubeconfig.
# =========================

log.info("Loading kubeconfig ...")
k8s_config.load_kube_config()
k8s = k8s_client.CoreV1Api()
log.info("Kubernetes client ready")

scheduler_logger = SchedulerLogger()

# =========================
# SSH HELPERS
# (memcached is a system service — taskset still goes over SSH)
# =========================

def ssh_cmd(host: str, remote_cmd: str) -> str:
    return f"ssh -i {SSH_KEY} {SSH_OPTS} ubuntu@{host} '{remote_cmd}'"


def run_remote(cmd: str) -> tuple[int, str, str]:
    log.debug(f"CMD: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout.strip():
        log.debug(f"STDOUT: {result.stdout.strip()}")
    if result.stderr.strip():
        log.debug(f"STDERR: {result.stderr.strip()}")
    if result.returncode != 0:
        log.error(f"Command exited with code {result.returncode}: {cmd}")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# =========================
# MEMCACHED PID (fetched remotely once at startup)
# =========================

log.info(f"Fetching memcached PID from {MEMCACHED_IP} ...")
_, _pid_out, _ = run_remote(ssh_cmd(MEMCACHED_IP, "pidof memcached"))
MEMCACHED_PID = int(_pid_out.split()[0])
log.info(f"memcached PID: {MEMCACHED_PID}")

# =========================
# MEMCACHED CONTROL
# (unchanged — taskset over SSH, memcached is not a pod)
# =========================

def set_memcached_cores(cores: list[int]) -> None:
    core_str = ",".join(map(str, cores))
    log.info(f"  → memcached: pinning to cores [{core_str}]")
    rc, _, stderr = run_remote(ssh_cmd(MEMCACHED_IP, f"sudo taskset -a -cp {core_str} {MEMCACHED_PID}"))
    if rc != 0:
        log.error(f"  ✗ taskset failed: {stderr}")
    else:
        log.info(f"  ✓ memcached cores set to [{core_str}]")
    scheduler_logger.update_cores(Job.MEMCACHED, cores)


# =========================
# KUBERNETES JOB CONTROL
#
# Each batch job runs as a Pod. Kubernetes doesn't support in-place
# cpuset updates (unlike Docker's container.update()), so to change
# CPU allocation we delete and recreate the pod with the new core
# count. The pod is pinned to BATCH_NODE via nodeSelector and given
# an explicit CPU request/limit matching the number of cores.
# =========================

def _pod_spec(job_enum, image: str, cores: list[int], threads: int) -> k8s_client.V1Pod:
    """Build a Pod manifest for a PARSEC/SPLASH job."""
    core_count = len(cores)
    return k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=job_enum.value,
            namespace=NAMESPACE,
            labels={"app": "parsec", "job": job_enum.value},
        ),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            node_selector={"kubernetes.io/hostname": BATCH_NODE},
            containers=[
                k8s_client.V1Container(
                    name=job_enum.value,
                    image=image,
                    command=[
                        "./run", "-a", "run", "-S", "parsec",
                        "-p", job_enum.value,
                        "-i", "native",
                        "-n", str(threads),
                    ],
                    resources=k8s_client.V1ResourceRequirements(
                        requests={"cpu": str(core_count)},
                        limits={"cpu": str(core_count)},
                    ),
                )
            ],
        ),
    )


def start_job(job_enum, image: str, cores: list[int], threads: int) -> str:
    """Create a Pod for the job. Returns the pod name."""
    log.info(f"  → Starting job {job_enum.value} | image={image} cores={cores} threads={threads}")
    pod = _pod_spec(job_enum, image, cores, threads)
    k8s.create_namespaced_pod(namespace=NAMESPACE, body=pod)
    log.info(f"  ✓ Pod {job_enum.value} created in namespace '{NAMESPACE}'")
    scheduler_logger.job_start(job_enum, cores, threads)
    return job_enum.value   # pod name == job name


def update_job_cores(pod_name: str, job_enum, cores: list[int]) -> None:
    """
    Kubernetes doesn't support in-place cpuset changes, so we
    delete the old pod and recreate it with the new CPU limit.
    The image is retrieved from the running pod before deletion.
    """
    log.info(f"  → Updating {job_enum.value} cores to {cores} (delete + recreate)")
    try:
        pod = k8s.read_namespaced_pod(name=pod_name, namespace=NAMESPACE)
        image = pod.spec.containers[0].image
        threads = int(next(
            a for a in pod.spec.containers[0].command
            if a.lstrip("-").isdigit()   # grab the -n <threads> value
        ))
    except ApiException as e:
        log.error(f"  ✗ Could not read pod {pod_name} before update: {e}")
        return

    # Delete old pod
    try:
        k8s.delete_namespaced_pod(name=pod_name, namespace=NAMESPACE)
        log.debug(f"  Pod {pod_name} deleted for core update")
    except ApiException as e:
        log.error(f"  ✗ Delete failed for {pod_name}: {e}")
        return

    # Wait for it to be gone
    for _ in range(20):
        try:
            k8s.read_namespaced_pod(name=pod_name, namespace=NAMESPACE)
            time.sleep(1)
        except ApiException:
            break   # pod is gone

    # Recreate with new core count
    pod = _pod_spec(job_enum, image, cores, threads)
    k8s.create_namespaced_pod(namespace=NAMESPACE, body=pod)
    log.info(f"  ✓ Pod {job_enum.value} recreated with {len(cores)} cores")
    scheduler_logger.update_cores(job_enum, cores)


def stop_job(pod_name: str, job_enum) -> None:
    log.info(f"  → Deleting pod {pod_name} ...")
    try:
        k8s.delete_namespaced_pod(name=pod_name, namespace=NAMESPACE)
        log.info(f"  ✓ Pod {pod_name} deleted")
    except ApiException as e:
        log.error(f"  ✗ Failed to delete pod {pod_name}: {e}")
    scheduler_logger.job_end(job_enum)


def pod_is_finished(pod_name: str) -> bool:
    """Return True if the pod has completed or no longer exists."""
    try:
        pod = k8s.read_namespaced_pod(name=pod_name, namespace=NAMESPACE)
        phase = pod.status.phase  # Pending | Running | Succeeded | Failed | Unknown
        log.debug(f"  Pod {pod_name} phase: {phase}")
        return phase in ("Succeeded", "Failed")
    except ApiException:
        return True   # pod gone — treat as finished


# =========================
# JOB DEFINITIONS
# =========================

JOB_MAP = [
    (Job.BLACKSCHOLES, "anakli/cca:parsec_blackscholes"),
    (Job.CANNEAL,      "anakli/cca:parsec_canneal"),
    (Job.FREQMINE,     "anakli/cca:parsec_freqmine"),
    (Job.RADIX,        "anakli/cca:splash2x_radix"),
    (Job.STREAMCLUSTER,"anakli/cca:parsec_streamcluster"),
    (Job.VIPS,         "anakli/cca:parsec_vips"),
    (Job.BARNES,       "anakli/cca:splash2x_barnes"),
]

job_queue    = JOB_MAP.copy()
running_jobs = []   # list of (pod_name: str, job_enum: Job)

# =========================
# CPU MONITORING (remote)
# =========================

def get_cpu() -> float:
    """Read CPU usage from the memcache server over SSH."""
    # Pass args as a list so no shell interprets the quotes —
    # the python3 snippet is handed to the remote shell verbatim.
    cmd = [
        "ssh", "-i", SSH_KEY, *SSH_OPTS.split(), f"ubuntu@{MEMCACHED_IP}",
        "python3", "-c", "import psutil; print(psutil.cpu_percent(interval=1))",
    ]
    log.debug(f"CMD: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    out = result.stdout.strip()
    if result.returncode != 0 or not out:
        log.error(f"get_cpu failed (rc={result.returncode}): {result.stderr.strip()}")
        return 0.0
    return float(out)


# =========================
# SCHEDULER
# =========================

def scheduler():
    log.info("=" * 60)
    log.info("Dynamic Scheduler starting")
    log.info(f"  Memcached IP  : {MEMCACHED_IP}")
    log.info(f"  Batch node    : {BATCH_NODE}")
    log.info(f"  Namespace     : {NAMESPACE}")
    log.info(f"  Total cores   : {TOTAL_CORES}")
    log.info(f"  Threads       : {THREADS}")
    log.info(f"  Jobs queued   : {[j.value for j, _ in job_queue]}")
    log.info("=" * 60)

    memcached_cores = [0, 1]
    scheduler_logger.job_start(Job.MEMCACHED, memcached_cores, THREADS)
    log.info(f"memcached starting on cores {memcached_cores}")
    set_memcached_cores(memcached_cores)

    batch_cores = [2, 3]
    iteration   = 0

    while True:
        iteration += 1
        cpu = get_cpu()
        log.info(
            f"[iter {iteration}] CPU: {cpu}%  |  "
            f"memcached_cores={memcached_cores}  batch_cores={batch_cores}  "
            f"running={[j.value for _, j in running_jobs]}  queued={len(job_queue)}"
        )

        # =====================
        # POLICY
        # =====================

        if cpu > 80:
            log.info("  High CPU (>80%) → prioritizing memcached")
            scheduler_logger.custom_event(Job.SCHEDULER, "High CPU → prioritize memcached")

            if len(memcached_cores) < 4 and batch_cores:
                moved = batch_cores.pop()
                memcached_cores.append(moved)
                log.info(f"  Core {moved}: batch → memcached  |  memcached={memcached_cores} batch={batch_cores}")
                set_memcached_cores(memcached_cores)
                for (pod_name, job_enum) in running_jobs:
                    update_job_cores(pod_name, job_enum, batch_cores)
            else:
                log.info("  No cores to move (memcached already at max or batch empty)")

        elif cpu < 50:
            log.info("  Low CPU (<50%) → giving resources to batch")
            scheduler_logger.custom_event(Job.SCHEDULER, "Low CPU → give resources to batch")

            if len(batch_cores) < 3 and len(memcached_cores) > 1:
                moved = memcached_cores.pop()
                batch_cores.append(moved)
                log.info(f"  Core {moved}: memcached → batch  |  memcached={memcached_cores} batch={batch_cores}")
                set_memcached_cores(memcached_cores)
            else:
                log.info("  No cores to move (batch already has 3 or memcached at minimum)")

        else:
            log.info("  CPU in range (50–80%) → no rebalancing")

        # =====================
        # START JOBS
        # =====================

        if job_queue and len(running_jobs) < 2:
            job_enum, image = job_queue.pop(0)
            log.info(f"Launching next job: {job_enum.value} ({len(job_queue)} remaining in queue)")
            pod_name = start_job(job_enum, image, batch_cores, threads=THREADS)
            running_jobs.append((pod_name, job_enum))

        # =====================
        # CLEANUP FINISHED
        # =====================

        still_running = []
        for (pod_name, job_enum) in running_jobs:
            if pod_is_finished(pod_name):
                log.info(f"  ✓ {job_enum.value} finished (pod {pod_name})")
                scheduler_logger.job_end(job_enum)
                # Clean up the completed pod
                try:
                    k8s.delete_namespaced_pod(name=pod_name, namespace=NAMESPACE)
                except ApiException:
                    pass
            else:
                still_running.append((pod_name, job_enum))

        running_jobs[:] = still_running

        # =====================
        # EXIT
        # =====================

        if not job_queue and not running_jobs:
            log.info("All jobs complete — exiting scheduler")
            break

        time.sleep(CHECK_INTERVAL)

    scheduler_logger.end()
    log.info("=" * 60)
    log.info("Scheduler finished")
    log.info(f"  Scheduler log : {scheduler_logger.get_file_name()}")
    log.info("=" * 60)


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()