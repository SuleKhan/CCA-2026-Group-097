from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from _master_logger import Job, SchedulerLogger

# =========================
# CONFIG
# =========================

CHECK_INTERVAL_SEC = 0.5
SWITCH_COOLDOWN_SEC = 0.5
QPS_INTERVAL_SEC = 5.0

# Configure the memcache node in one place.  The same address is used for
# Fabric SSH control commands and remote Docker commands.
MEMCACHE_IP = "35.241.228.86"
MEMCACHE_USER = "ubuntu"
MEMCACHE_SSH_KEY = ".ssh/master_node_key"

# The measure node redraws QPS every QPS_INTERVAL_SEC seconds.  CPU decisions
# use only samples collected inside the current QPS phase, so stale pressure from
# the previous phase cannot keep the scheduler in a conservative mode.
#
# Mode switching uses hysteresis: the upper threshold moves to a more
# conservative mode, while the lower threshold moves back to a less conservative
# mode.  Each mode boundary has its own threshold pair so FAST <-> SAFE and
# SAFE <-> FULL_MEMCACHE can be tuned independently.

FAST2SAFE_LOWER_THRESHOLD = 60.0
FAST2SAFE_UPPER_THRESHOLD = 75.0
SAFE2FULL_LOWER_THRESHOLD = FAST2SAFE_LOWER_THRESHOLD + 10
SAFE2FULL_UPPER_THRESHOLD = FAST2SAFE_UPPER_THRESHOLD + 10

JOB_THREADS = 2
CPU_LOG_FILE = "cpu_usage.log"

# Four-core memcached host policy.
MEMCACHED_FAST_CORES = [0, 1]
MEMCACHED_SAFE_CORES = [0, 1, 2]
MEMCACHED_FULL_CORES = [0, 1, 2, 3]
JOB_FAST_CORES = [2, 3]
JOB_SAFE_CORES = [3]
JOB_PAUSED_CORES: list[int] = []



class SchedulerMode(Enum):
    FAST = "FAST"
    SAFE = "SAFE"
    FULL_MEMCACHE = "FULL_MEMCACHE"


@dataclass
class FreshCpuWindow:
    """CPU samples collected only within the current QPS interval."""

    samples: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.samples.clear()

    def add(self, value: float) -> None:
        self.samples.append(value)

    @property
    def average(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)


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

    def reset(self) -> None:
        self.prev = None

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

    def pause(self) -> None:
        self.conn.run("docker pause " + shlex.quote(self.name), hide=True)
        self.status = "paused"

    def unpause(self) -> None:
        self.conn.run("docker unpause " + shlex.quote(self.name), hide=True)
        self.status = "running"


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




def pause_job(container: RemoteDockerContainer, job: Job, logger: SchedulerLogger) -> None:
    if container.status != "paused":
        container.pause()
    logger.update_cores(job, JOB_PAUSED_CORES)


def resume_job(container: RemoteDockerContainer, job: Job, cores: list[int], logger: SchedulerLogger) -> None:
    if container.status == "paused":
        # Update the cpuset before unpausing so the job never briefly resumes on
        # stale cores from the previous mode.
        container.update(cpuset_cpus=",".join(map(str, cores)))
        logger.update_cores(job, cores)
        container.unpause()
    else:
        apply_job_cores(container, job, cores, logger)


def mode_memcached_cores(mode: SchedulerMode) -> list[int]:
    if mode == SchedulerMode.FAST:
        return MEMCACHED_FAST_CORES.copy()
    if mode == SchedulerMode.SAFE:
        return MEMCACHED_SAFE_CORES.copy()
    return MEMCACHED_FULL_CORES.copy()


def mode_job_cores(mode: SchedulerMode) -> list[int]:
    if mode == SchedulerMode.FAST:
        return JOB_FAST_CORES.copy()
    if mode == SchedulerMode.SAFE:
        return JOB_SAFE_CORES.copy()
    return JOB_PAUSED_CORES.copy()


def next_mode(current: SchedulerMode, fresh_cpu: float) -> SchedulerMode:
    # Hysteresis avoids rapid mode flapping around a single CPU percentage.
    #
    # FAST <-> SAFE uses FAST_SAFE_* thresholds:
    #   - FAST promotes to SAFE only at/above FAST_SAFE_UPPER_THRESHOLD.
    #   - SAFE demotes to FAST only below FAST_SAFE_LOWER_THRESHOLD.
    #
    # SAFE <-> FULL_MEMCACHE uses SAFE_FULL_* thresholds:
    #   - SAFE promotes to FULL_MEMCACHE only at/above SAFE_FULL_UPPER_THRESHOLD.
    #   - FULL_MEMCACHE demotes to SAFE only below SAFE_FULL_LOWER_THRESHOLD.
    if current == SchedulerMode.FAST:
        if fresh_cpu >= FAST2SAFE_UPPER_THRESHOLD:
            return SchedulerMode.SAFE
        return SchedulerMode.FAST

    if current == SchedulerMode.SAFE:
        if fresh_cpu >= SAFE2FULL_UPPER_THRESHOLD:
            return SchedulerMode.FULL_MEMCACHE
        if fresh_cpu < FAST2SAFE_LOWER_THRESHOLD:
            return SchedulerMode.FAST
        return SchedulerMode.SAFE

    if fresh_cpu < SAFE2FULL_LOWER_THRESHOLD:
        return SchedulerMode.SAFE
    return SchedulerMode.FULL_MEMCACHE


def apply_mode(
    mode: SchedulerMode,
    container: RemoteDockerContainer,
    job: Job,
    memcached_pid: int,
    mem_conn: RemoteShell,
    logger: SchedulerLogger,
) -> tuple[list[int], list[int]]:
    memcached_cores = mode_memcached_cores(mode)
    job_cores = mode_job_cores(mode)

    if mode == SchedulerMode.FAST:
        set_memcached_cores(memcached_cores, memcached_pid, mem_conn, logger)
        resume_job(container, job, job_cores, logger)
    elif mode == SchedulerMode.SAFE:
        if container.status == "paused":
            container.update(cpuset_cpus=",".join(map(str, job_cores)))
            logger.update_cores(job, job_cores)
            set_memcached_cores(memcached_cores, memcached_pid, mem_conn, logger)
            container.unpause()
        else:
            resume_job(container, job, job_cores, logger)
            set_memcached_cores(memcached_cores, memcached_pid, mem_conn, logger)
    else:
        pause_job(container, job, logger)
        set_memcached_cores(memcached_cores, memcached_pid, mem_conn, logger)

    return memcached_cores, job_cores


def log_cpu(
    raw_cpu: float,
    fresh_cpu: float,
    mode: SchedulerMode,
    memcached_cores: list[int],
    job_cores: list[int],
) -> None:
    timestamp = datetime.now().isoformat()
    job_core_str = "paused" if not job_cores else ",".join(map(str, job_cores))
    with open(CPU_LOG_FILE, "a") as f:
        f.write(
            f"{timestamp},raw_memcached={raw_cpu:.2f},fresh_memcached={fresh_cpu:.2f},"
            f"mode={mode.value},"
            f"memcached={','.join(map(str, memcached_cores))},"
            f"job={job_core_str}\n"
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
    container: RemoteDockerContainer | None = None

    mode = SchedulerMode.FAST
    memcached_cores = MEMCACHED_FAST_CORES.copy()
    job_cores = JOB_FAST_CORES.copy()
    last_switch = 0.0
    cpu_window = FreshCpuWindow()
    phase_start = time.monotonic()
    phase_index = 0

    try:
        memcached_pid = preflight(mem_conn)
        sampler = MemcachedCpuSampler(mem_conn, memcached_pid)

        # Start in FAST: memcached gets two protected cores and the batch job
        # gets the other two cores for maximum throughput.
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
                    memcached_cores = MEMCACHED_FULL_CORES.copy()
                    job_cores = JOB_PAUSED_CORES.copy()

                    print("No jobs left; giving all cores to memcached")
                    set_memcached_cores(memcached_cores, memcached_pid, mem_conn, logger)
                    break

                # New jobs start with the current policy's job cores.  If the
                # node is in FULL_MEMCACHE, start the job on a minimal cpuset
                # and pause it immediately so memcached keeps all four cores.
                current_job, image = job_queue.pop(0)
                start_cores = JOB_SAFE_CORES if mode == SchedulerMode.FULL_MEMCACHE else job_cores
                print(f"Starting {current_job.value} on memcache host cores {start_cores}")
                container = start_job(current_job, image, start_cores, JOB_THREADS, mem_conn, logger)
                if mode == SchedulerMode.FULL_MEMCACHE:
                    pause_job(container, current_job, logger)
                    job_cores = JOB_PAUSED_CORES.copy()

            now_mono = time.monotonic()
            current_phase_index = int((now_mono - phase_start) // QPS_INTERVAL_SEC)
            phase_boundary = current_phase_index != phase_index
            if phase_boundary:
                phase_index = current_phase_index
                cpu_window.reset()
                sampler.reset()
                sampler.utilization_percent(memcached_cores)  # seed this phase only
                last_switch = 0.0  # allow immediate switching in the new phase
                # print("New QPS phase detected; reset fresh CPU window")
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            raw_cpu = sampler.utilization_percent(memcached_cores)
            cpu_window.add(raw_cpu)
            fresh_cpu = cpu_window.average

            pressure_threshold = (
                FAST2SAFE_UPPER_THRESHOLD
                if mode == SchedulerMode.FAST
                else SAFE2FULL_UPPER_THRESHOLD
            )
            if fresh_cpu >= pressure_threshold:
                print(
                    f"[WARNING]: Memcached pressure {fresh_cpu:.1f}% "
                    f"over fresh window on {len(memcached_cores)} cores."
                )

            now = time.time()
            log_cpu(raw_cpu, fresh_cpu, mode, memcached_cores, job_cores)

            can_switch = (now - last_switch) >= SWITCH_COOLDOWN_SEC or phase_boundary
            desired_mode = next_mode(mode, fresh_cpu)

            if can_switch and desired_mode != mode:
                print(
                    f"Mode {mode.value} -> {desired_mode.value}; "
                    f"fresh memcached CPU {fresh_cpu:.1f}%"
                )
                mode = desired_mode
                memcached_cores, job_cores = apply_mode(
                    mode,
                    container,
                    current_job,
                    memcached_pid,
                    mem_conn,
                    logger,
                )
                sampler.reset()
                sampler.utilization_percent(memcached_cores)  # reset after capacity change
                cpu_window.reset()
                last_switch = now

            time.sleep(CHECK_INTERVAL_SEC)

    finally:
        try:
            if memcached_logged_started:
                try:
                    set_memcached_cores(
                        MEMCACHED_FULL_CORES.copy(),
                        memcached_pid,
                        mem_conn,
                        logger,
                    )
                except Exception as e:
                    print(f"[cleanup warning] Could not restore memcached cores: {e}")

                logger.job_end(Job.MEMCACHED)

            logger.end()
        finally:
            mem_conn.close()


if __name__ == "__main__":
    scheduler()
