from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from _master_logger import Job, SchedulerLogger

# =========================
# CONFIG
# =========================

CHECK_INTERVAL_SEC = 0.5
SWITCH_COOLDOWN_SEC = 2.0

# Configure the memcache node in one place.  The same address is used for
# Fabric SSH control commands and remote Docker commands.
MEMCACHE_IP = "35.241.183.126"
MEMCACHE_USER = "ubuntu"
MEMCACHE_SSH_KEY = ".ssh/master_node_key"

# The measure node changes QPS every 15 seconds.  Hysteresis keeps us from
# thrashing while still reacting inside each QPS phase.
MEMCACHE_HIGH_UTIL = 75.0
MEMCACHE_LOW_UTIL = 55.0
EWMA_ALPHA = 0.40

JOB_THREADS = 2
CPU_LOG_FILE = "cpu_usage.log"

# Four-core memcached host policy:
# - Core 2 is the elastic core.
# - When memcached is cool, jobs use cores 2 and 3 to finish faster.
# - When memcached is hot, jobs are shrunk to core 3 and memcached gets core 2.
MEMCACHED_BASE_CORES = [0, 1]
MEMCACHED_BOOST_CORES = [0, 1, 2]
JOB_FAST_CORES = [2, 3]
JOB_SAFE_CORES = [3]

NODES = {
    "memcache": {
        "host": MEMCACHE_IP,
        "user": MEMCACHE_USER,
        "key": MEMCACHE_SSH_KEY,
        # Docker jobs run on the memcached server through this SSH connection.
    },
}


@dataclass
class RemoteResult:
    stdout: str


class RemoteShell:
    """Run remote commands with the system ssh client.

    This intentionally uses OpenSSH instead of Paramiko/Fabric, so it behaves
    the same as the manual command that works:

        ssh -i ~/.ssh/master_node_key ubuntu@<memcache-ip>
    """

    def __init__(self, host: str, user: str, key: str) -> None:
        self.host = host
        self.user = user
        self.key = key

    def run(self, command: str, hide: bool = False) -> RemoteResult:
        ssh_cmd = [
            "ssh",
            "-i",
            self.key,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{self.user}@{self.host}",
            command,
        ]
        proc = subprocess.run(
            ssh_cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            msg = proc.stderr.strip() or proc.stdout.strip() or f"exit status {proc.returncode}"
            raise RuntimeError(f"Remote command failed: {command}\n{msg}")
        if not hide and proc.stdout:
            print(proc.stdout, end="")
        return RemoteResult(stdout=proc.stdout)

    def close(self) -> None:
        return None

JOB_MAP = [
    (Job.BLACKSCHOLES, "anakli/cca:parsec_blackscholes"),
    (Job.CANNEAL, "anakli/cca:parsec_canneal"),
    (Job.FREQMINE, "anakli/cca:parsec_freqmine"),
    (Job.RADIX, "anakli/cca:splash2x_radix"),
    (Job.STREAMCLUSTER, "anakli/cca:parsec_streamcluster"),
    (Job.VIPS, "anakli/cca:parsec_vips"),
    (Job.BARNES, "anakli/cca:splash2x_barnes"),
]


# =========================
# MEMCACHED CPU SAMPLING
# =========================

@dataclass
class ProcCpuSnapshot:
    proc_jiffies: int
    wall_time: float


class MemcachedCpuSampler:
    """Measure memcached process CPU use as % of its currently allocated cores."""

    def __init__(self, conn: RemoteShell, memcached_pid: int) -> None:
        self.conn = conn
        self.memcached_pid = memcached_pid
        self.clk_tck = int(conn.run("getconf CLK_TCK", hide=True).stdout.strip())
        self.prev: Optional[ProcCpuSnapshot] = None

    def _read_proc_jiffies(self) -> int:
        # Fields 14 and 15 are utime and stime. awk handles the simple numeric read remotely.
        cmd = f"awk '{{print $14 + $15}}' /proc/{self.memcached_pid}/stat"
        result = self.conn.run(cmd, hide=True)
        return int(result.stdout.strip())

    def utilization_percent(self, allocated_cores: list[int]) -> float:
        now = ProcCpuSnapshot(
            proc_jiffies=self._read_proc_jiffies(),
            wall_time=time.monotonic(),
        )

        if self.prev is None:
            self.prev = now
            return 0.0

        delta_jiffies = now.proc_jiffies - self.prev.proc_jiffies
        delta_wall = now.wall_time - self.prev.wall_time
        self.prev = now

        if delta_wall <= 0 or not allocated_cores:
            return 0.0

        cpu_seconds = delta_jiffies / self.clk_tck
        capacity_seconds = delta_wall * len(allocated_cores)
        return max(0.0, min(100.0, 100.0 * cpu_seconds / capacity_seconds))


# =========================
# REMOTE CONTROL ON MEMCACHE SERVER
# =========================

_current_memcached_cores: list[int] | None = None


def set_memcached_cores(
    cores: list[int],
    memcached_pid: int,
    mem_conn: RemoteShell,
    logger: SchedulerLogger,
) -> None:
    global _current_memcached_cores
    if cores == _current_memcached_cores:
        return

    core_str = ",".join(map(str, cores))
    mem_conn.run(f"sudo -n taskset -a -cp {core_str} {memcached_pid}", hide=True)
    logger.update_cores(Job.MEMCACHED, cores)
    _current_memcached_cores = cores.copy()


class RemoteDockerContainer:
    """Tiny Docker wrapper that runs Docker commands over the existing SSH connection."""

    def __init__(self, conn: RemoteShell, name: str) -> None:
        self.conn = conn
        self.name = name
        self.status: str | None = None

    def reload(self) -> None:
        result = self.conn.run(
            "docker inspect -f {{.State.Status}} " + shlex.quote(self.name),
            hide=True,
        )
        self.status = result.stdout.strip()

    def update(self, cpuset_cpus: str) -> None:
        self.conn.run("docker update --cpuset-cpus " + shlex.quote(cpuset_cpus) + " " + shlex.quote(self.name), hide=True)


def start_job(
    job: Job,
    image: str,
    cores: list[int],
    JOB_threads: int,
    mem_conn: RemoteShell,
    logger: SchedulerLogger,
) -> RemoteDockerContainer:
    core_str = ",".join(map(str, cores))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"scheduler-{job.value}-{run_id}"
    command = f"./run -a run -S parsec -p {job.value} -i native -n {JOB_threads}"

    mem_conn.run(
        " ".join(
            [
                "docker",
                "run",
                "-d",
                "--name",
                shlex.quote(name),
                "--cpuset-cpus",
                shlex.quote(core_str),
                shlex.quote(image),
                command,
            ]
        ),
        hide=True,
    )
    logger.job_start(job, cores, JOB_threads)
    return RemoteDockerContainer(mem_conn, name)


def apply_job_cores(container: RemoteDockerContainer, job: Job, cores: list[int], logger: SchedulerLogger) -> None:
    container.update(cpuset_cpus=",".join(map(str, cores)))
    logger.update_cores(job, cores)


def log_cpu(raw_cpu: float, ewma_cpu: float, memcached_cores: list[int], job_cores: list[int]) -> None:
    timestamp = datetime.now().isoformat()
    with open(CPU_LOG_FILE, "a") as f:
        f.write(
            f"{timestamp},raw_memcached={raw_cpu:.2f},ewma_memcached={ewma_cpu:.2f},"
            f"memcached={','.join(map(str, memcached_cores))},"
            f"job={','.join(map(str, job_cores))}\n"
        )


def preflight(mem_conn: RemoteShell) -> int:
    print("[preflight] Checking SSH connectivity to memcache node …")
    mem_conn.run("true", hide=True)

    print("[preflight] Checking Docker on memcache node over SSH …")
    mem_conn.run("docker version --format '{{.Server.Version}}'", hide=True)

    print("[preflight] Resolving memcached PID …")
    result = mem_conn.run("pidof memcached", hide=True)
    pids = result.stdout.strip().split()
    if not pids:
        raise RuntimeError("memcached does not appear to be running on the memcache node")

    pid = int(pids[0])
    print(f"[preflight] memcached PID = {pid}")

    print("[preflight] Verifying passwordless sudo for taskset …")
    mem_conn.run(f"sudo -n taskset -a -pc {pid}", hide=True)

    print("[preflight] All checks passed.\n")
    return pid


# =========================
# SCHEDULER
# =========================


def scheduler() -> None:
    mem_cfg = NODES["memcache"]
    mem_conn = RemoteShell(
        host=mem_cfg["host"],
        user=mem_cfg["user"],
        key=mem_cfg["key"],
    )
    logger = SchedulerLogger()

    memcached_logged_started = False
    current_job: Job | None = None
    container = None

    memcached_cores = MEMCACHED_BASE_CORES.copy()
    job_cores = JOB_FAST_CORES.copy()
    last_switch = 0.0
    ewma_cpu: float | None = None

    try:
        memcached_pid = preflight(mem_conn)
        sampler = MemcachedCpuSampler(mem_conn, memcached_pid)

        # Start with memcached protected on its baseline cores and jobs using the
        # spare cores for maximum throughput.
        logger.job_start(Job.MEMCACHED, memcached_cores, JOB_THREADS)
        memcached_logged_started = True
        set_memcached_cores(memcached_cores, memcached_pid, mem_conn, logger)
        sampler.utilization_percent(memcached_cores)  # seed baseline

        job_queue = JOB_MAP.copy()
        if not job_queue:
            return

        current_job, image = job_queue.pop(0)
        print(f"Starting {current_job.value} on memcache host cores {job_cores}")
        container = start_job(current_job, image, job_cores, JOB_THREADS, mem_conn, logger)

        while container is not None:
            container.reload()

            if container.status == "exited":
                logger.job_end(current_job)
                print(f"Finished {current_job.value}")

                if not job_queue:
                    break

                # New jobs start with the current policy's job cores.
                current_job, image = job_queue.pop(0)
                print(f"Starting {current_job.value} on memcache host cores {job_cores}")
                container = start_job(current_job, image, job_cores, JOB_THREADS, mem_conn, logger)

            raw_cpu = sampler.utilization_percent(memcached_cores)
            if (len(memcached_cores) == 2 and raw_cpu >=75) or (len(memcached_cores) == 3 and raw_cpu >=80):
                print(f"[WARNING]: Potential SLO Violation, measured {raw_cpu} for {len(memcached_cores)} cores.")
            ewma_cpu = raw_cpu if ewma_cpu is None else EWMA_ALPHA * raw_cpu + (1.0 - EWMA_ALPHA) * ewma_cpu
            now = time.time()
            log_cpu(raw_cpu, ewma_cpu, memcached_cores, job_cores)

            can_switch = (now - last_switch) >= SWITCH_COOLDOWN_SEC

            if can_switch and memcached_cores == MEMCACHED_BASE_CORES and ewma_cpu >= MEMCACHE_HIGH_UTIL:
                # Protect SLO: first move the job off elastic core 2, then give it to memcached.
                print(
                    f"Memcached hot ({ewma_cpu:.1f}% of {memcached_cores}); "
                    f"shrinking {current_job.value} to {JOB_SAFE_CORES} and boosting memcached"
                )
                if job_cores != JOB_SAFE_CORES:
                    apply_job_cores(container, current_job, JOB_SAFE_CORES, logger)
                    job_cores = JOB_SAFE_CORES.copy()
                set_memcached_cores(MEMCACHED_BOOST_CORES, memcached_pid, mem_conn, logger)
                memcached_cores = MEMCACHED_BOOST_CORES.copy()
                sampler.utilization_percent(memcached_cores)  # reset sampler after capacity change
                last_switch = now

            elif can_switch and memcached_cores == MEMCACHED_BOOST_CORES and ewma_cpu <= MEMCACHE_LOW_UTIL:
                # Recover throughput: first remove memcached from elastic core 2, then expand the job.
                print(
                    f"Memcached cool ({ewma_cpu:.1f}% of {memcached_cores}); "
                    f"returning memcached to {MEMCACHED_BASE_CORES} and expanding {current_job.value}"
                )
                set_memcached_cores(MEMCACHED_BASE_CORES, memcached_pid, mem_conn, logger)
                memcached_cores = MEMCACHED_BASE_CORES.copy()
                sampler.utilization_percent(memcached_cores)  # reset sampler after capacity change
                if job_cores != JOB_FAST_CORES:
                    apply_job_cores(container, current_job, JOB_FAST_CORES, logger)
                    job_cores = JOB_FAST_CORES.copy()
                last_switch = now

            time.sleep(CHECK_INTERVAL_SEC)

    finally:
        if memcached_logged_started:
            logger.job_end(Job.MEMCACHED)
        logger.end()
        try:
            mem_conn.close()
        finally:
            pass


if __name__ == "__main__":
    scheduler()
