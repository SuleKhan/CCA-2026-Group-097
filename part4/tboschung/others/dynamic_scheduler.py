import psutil
import subprocess
import time

from scheduler_logger import SchedulerLogger, Job

# =========================
# CONFIG
# =========================

CHECK_INTERVAL = 5
TOTAL_CORES = [0, 1, 2, 3]

# Get memcached PID
MEMCACHED_PID = int(subprocess.getoutput("pidof memcached").split()[0])

logger = SchedulerLogger()

# =========================
# MEMCACHED CONTROL
# =========================

def set_memcached_cores(cores):
    core_str = ",".join(map(str, cores))
    cmd = f"sudo taskset -a -cp {core_str} {MEMCACHED_PID}"
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL)

    logger.update_cores(Job.MEMCACHED, cores)


# =========================
# KUBECTL JOB CONTROL
# =========================

def run_cmd(cmd, check=True, input=None):
    p = subprocess.run(cmd, text=True, input=input, capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"Command failed: {cmd}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return p


def apply_manifest(text):
    out = run_cmd(["kubectl", "apply", "-f", "-"], input=text).stdout.strip()
    if out:
        print(out)


def start_job(job_enum, manifest, cores, threads):
    apply_manifest(manifest)
    logger.job_start(job_enum, cores, threads)


def get_job_phase(job_name):
    return run_cmd(
        ["kubectl", "get", "pods", f"--selector=job-name={job_name}",
         "-o", "jsonpath={.items[0].status.phase}"],
        check=False
    ).stdout.strip() or "Unknown"


def stop_job(job_name, job_enum):
    run_cmd(["kubectl", "delete", "job", job_name, "--ignore-not-found=true"], check=False)
    logger.job_end(job_enum)


# =========================
# JOB DEFINITIONS (inline YAML)
# =========================

BLACKSCHOLES_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-blackscholes, labels: {app: parsec-blackscholes}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-blackscholes}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: blackscholes
        image: anakli/cca:parsec_blackscholes
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-3 ./run -a run -S parsec -p blackscholes -i native -n 4"]
        resources:
          requests: {cpu: "4", memory: "2Gi"}
          limits: {cpu: "4", memory: "2Gi"}
""".strip()

CANNEAL_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-canneal, labels: {app: parsec-canneal}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-canneal}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-b-4core}
      containers:
      - name: canneal
        image: anakli/cca:parsec_canneal
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 1-2 ./run -a run -S parsec -p canneal -i native -n 2"]
        resources:
          requests: {cpu: "2", memory: "2Gi"}
          limits: {cpu: "2", memory: "2Gi"}
""".strip()

FREQMINE_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-freqmine, labels: {app: parsec-freqmine}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-freqmine}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: freqmine
        image: anakli/cca:parsec_freqmine
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-6 ./run -a run -S parsec -p freqmine -i native -n 7"]
        resources:
          requests: {cpu: "7", memory: "4Gi"}
          limits: {cpu: "7", memory: "4Gi"}
""".strip()

RADIX_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-radix, labels: {app: parsec-radix}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-radix}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: radix
        image: anakli/cca:splash2x_radix
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-3 ./run -a run -S splash2x -p radix -i native -n 4"]
        resources:
          requests: {cpu: "4", memory: "8Gi"}
          limits: {cpu: "4", memory: "8Gi"}
""".strip()

STREAMCLUSTER_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-streamcluster, labels: {app: parsec-streamcluster}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-streamcluster}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: streamcluster
        image: anakli/cca:parsec_streamcluster
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-6 ./run -a run -S parsec -p streamcluster -i native -n 7"]
        resources:
          requests: {cpu: "7", memory: "4Gi"}
          limits: {cpu: "7", memory: "4Gi"}
""".strip()

VIPS_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-vips, labels: {app: parsec-vips}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-vips}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-b-4core}
      containers:
      - name: vips
        image: anakli/cca:parsec_vips
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 1-2 ./run -a run -S parsec -p vips -i native -n 2"]
        resources:
          requests: {cpu: "2", memory: "2Gi"}
          limits: {cpu: "2", memory: "2Gi"}
""".strip()

BARNES_YAML = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-barnes, labels: {app: parsec-barnes}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-barnes}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: barnes
        image: anakli/cca:splash2x_barnes
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-6 ./run -a run -S splash2x -p barnes -i native -n 7"]
        resources:
          requests: {cpu: "7", memory: "4Gi"}
          limits: {cpu: "7", memory: "4Gi"}
""".strip()


# Each entry: (Job enum, k8s job name, manifest, cores, threads)
JOB_MAP = [
    (Job.BLACKSCHOLES, "parsec-blackscholes", BLACKSCHOLES_YAML, [0, 1, 2, 3], 4),
    (Job.CANNEAL,      "parsec-canneal",      CANNEAL_YAML,      [1, 2],       2),
    (Job.FREQMINE,     "parsec-freqmine",     FREQMINE_YAML,     [0, 1, 2, 3, 4, 5, 6], 7),
    (Job.RADIX,        "parsec-radix",        RADIX_YAML,        [0, 1, 2, 3], 4),
    (Job.STREAMCLUSTER,"parsec-streamcluster",STREAMCLUSTER_YAML,[0, 1, 2, 3, 4, 5, 6], 7),
    (Job.VIPS,         "parsec-vips",         VIPS_YAML,         [1, 2],       2),
    (Job.BARNES,       "parsec-barnes",       BARNES_YAML,       [0, 1, 2, 3, 4, 5, 6], 7),
]

job_queue = JOB_MAP.copy()
# running_jobs entries: (job_name, job_enum)
running_jobs = []

# =========================
# CPU MONITORING
# =========================

def get_cpu():
    return psutil.cpu_percent(interval=1)


# =========================
# SCHEDULER
# =========================

def scheduler():

    # IMPORTANT: log memcached start
    memcached_cores = [0, 1]
    logger.job_start(Job.MEMCACHED, memcached_cores, 2)

    set_memcached_cores(memcached_cores)

    batch_cores = [2, 3]

    while True:
        cpu = get_cpu()
        print(f"CPU: {cpu}%")

        # =====================
        # POLICY
        # =====================

        if cpu > 80:
            logger.custom_event(Job.SCHEDULER, "High CPU → prioritize memcached")

            if len(memcached_cores) < 4 and batch_cores:
                memcached_cores.append(batch_cores.pop())
                set_memcached_cores(memcached_cores)

        elif cpu < 50:
            logger.custom_event(Job.SCHEDULER, "Low CPU → give resources to batch")

            if len(batch_cores) < 3 and len(memcached_cores) > 1:
                batch_cores.append(memcached_cores.pop())
                set_memcached_cores(memcached_cores)

        # =====================
        # START JOBS
        # =====================

        if job_queue and len(running_jobs) < 2:
            job_enum, job_name, manifest, cores, threads = job_queue.pop(0)
            start_job(job_enum, manifest, cores, threads)
            running_jobs.append((job_name, job_enum))

        # =====================
        # CLEANUP FINISHED
        # =====================

        still_running = []
        for (job_name, job_enum) in running_jobs:
            phase = get_job_phase(job_name)
            if phase == "Succeeded":
                logger.job_end(job_enum)
            elif phase == "Failed":
                raise RuntimeError(f"Job failed: {job_name}")
            else:
                still_running.append((job_name, job_enum))

        running_jobs[:] = still_running

        # =====================
        # EXIT
        # =====================

        if not job_queue and not running_jobs:
            break

        time.sleep(CHECK_INTERVAL)

    logger.end()


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()