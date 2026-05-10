#!/usr/bin/env python3
"""
Memcached interference benchmark.

Compares memcached performance under core configurations, including:

    1. memcached on [0,1,2] with core 3 idle
    2. memcached on [0,1,2] with a background interference job pinned to core 3

The interference job is intentionally simple:
    - preferred: memory-touching Python loop pinned to core 3
    - fallback: CPU spin loop pinned to core 3

This helps test whether the scheduler's "safe mode":

    memcached = [0,1,2]
    job       = [3]

is actually safe for the SLO.
"""

import argparse
import logging
import os
import shlex
import subprocess
import time
from datetime import datetime
from itertools import product

from _ip_parser import (
    MEMCACHED_IP,
    MEASURE_IP,
    INTERNAL_MEMCACHED_IP,
    INTERNAL_AGENT_IP,
    NODES,
)
# print(NODES.all_ips())
# print(NODES.external_ip("master"))
# print(NODES.internal_ip("client-measure"))


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────


SSH_KEY = "~/.ssh/cloud-computing"
SSH_OPTS = "-o StrictHostKeyChecking=no"

THREAD_CONFIGS = [3]

CORE_CONFIGS = [
    # [0],
    # [0, 1],
    [0, 1, 2],
]

# Only meaningful when memcached does NOT use core 3.
INTERFERENCE_MODES = [
    "none",
    "memory",
]

REPETITIONS = 3

MCPERF_PARAMS = "-T 8 -C 8 -D 4 -Q 1000 -c 8 -t 120 --qps_interval 10 --qps_min 5000 --qps_max 110000"

SLEEP_BETWEEN_REPS = 5

INTERFERENCE_CORE = 3
INTERFERENCE_REMOTE_PID = "~/interference_core3.pid"


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def setup_logging(log_dir: str) -> tuple[logging.Logger, str]:
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"benchmark_{timestamp}.log")

    logger = logging.getLogger("benchmark")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Logging to: {log_file}")
    return logger, log_file


# ─────────────────────────────────────────────
# SSH / PROCESS HELPERS
# ─────────────────────────────────────────────

def ssh_cmd(host: str, remote_cmd: str) -> str:
    key_path = os.path.expanduser(SSH_KEY)
    return f"ssh -i {shlex.quote(key_path)} {SSH_OPTS} ubuntu@{host} {shlex.quote(remote_cmd)}"


def run(cmd: str, logger: logging.Logger, dry_run: bool = False) -> tuple[int, str, str]:
    logger.debug(f"CMD: {cmd}")

    if dry_run:
        logger.info("[DRY-RUN] Skipping execution.")
        return 0, "[dry-run]", ""

    result = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.stdout.strip():
        logger.debug(f"STDOUT:\n{result.stdout.strip()}")

    if result.stderr.strip():
        logger.debug(f"STDERR:\n{result.stderr.strip()}")

    if result.returncode != 0:
        logger.error(f"Command failed with rc={result.returncode}: {cmd}")

    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ─────────────────────────────────────────────
# MEMCACHED CONFIGURATION
# ─────────────────────────────────────────────

def set_threads_and_cores(
    threads: int,
    cores: list[int],
    logger: logging.Logger,
    dry_run: bool,
) -> bool:
    cores_str = ",".join(str(c) for c in cores)

    logger.info(f"  → Setting memcached threads={threads}")
    update_threads_cmd = (
        f"sudo sed -i -E \"s/^-t .*/-t {threads}/\" /etc/memcached.conf"
    )
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, update_threads_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to update memcached threads: {stderr}")
        return False

    logger.info(f"  → Setting memcached AllowedCPUs={cores_str}")
    set_cpu_cmd = f"sudo systemctl set-property memcached AllowedCPUs={cores_str}"
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, set_cpu_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to set AllowedCPUs: {stderr}")
        return False

    logger.info("  → Restarting memcached")
    restart_cmd = "sudo systemctl restart memcached"
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, restart_cmd), logger, dry_run)
    if rc != 0:
        logger.warning(f"  ⚠ Restart returned nonzero: {stderr}")

    if not dry_run:
        time.sleep(4)

    logger.info("  → Verifying memcached affinity")
    verify_cmd = "taskset -cp $(pgrep -o memcached)"
    rc, stdout, stderr = run(ssh_cmd(MEMCACHED_IP, verify_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to verify affinity: {stderr}")
        return False

    logger.info(f"  ✓ Affinity check: {stdout}")

    expected = "0-2" if cores == [0, 1, 2] else cores_str
    if expected not in stdout:
        logger.error(f"  ✗ Affinity mismatch. Expected {cores_str}, got: {stdout}")
        return False

    return True


# ─────────────────────────────────────────────
# INTERFERENCE JOB
# ─────────────────────────────────────────────

def stop_interference(logger: logging.Logger, dry_run: bool) -> None:
    stop_cmd = (
        f"if [ -f {INTERFERENCE_REMOTE_PID} ]; then "
        f"kill $(cat {INTERFERENCE_REMOTE_PID}) 2>/dev/null || true; "
        f"rm -f {INTERFERENCE_REMOTE_PID}; "
        f"fi; "
        f"pkill -f interference_memory_core3.py 2>/dev/null || true"
    )
    run(ssh_cmd(MEMCACHED_IP, stop_cmd), logger, dry_run)


def start_interference(mode: str, logger: logging.Logger, dry_run: bool) -> bool:
    """
    Starts an artificial background job pinned to core 3.

    mode="none":
        no interference

    mode="memory":
        Python loop that repeatedly touches a large bytearray.
        This is meant to create memory/cache pressure, not just CPU pressure.
    """
    stop_interference(logger, dry_run)

    if mode == "none":
        logger.info("  → No interference job")
        return True

    if mode != "memory":
        raise ValueError(f"Unknown interference mode: {mode}")

    logger.info(f"  → Starting memory interference on core {INTERFERENCE_CORE}")

    remote_script = "~/interference_memory_core3.py"

    script = r"""
import time

# Allocate a large buffer and repeatedly touch it.
# Keep this below total memory to avoid swapping.
SIZE_MB = 512
STRIDE = 4096

buf = bytearray(SIZE_MB * 1024 * 1024)
i = 0

while True:
    for off in range(0, len(buf), STRIDE):
        buf[off] = (buf[off] + 1) & 0xFF
    i += 1
"""

    create_and_run_cmd = f"""
cat > {remote_script} <<'PYEOF'
{script}
PYEOF

nohup taskset -c {INTERFERENCE_CORE} python3 {remote_script} \\
    >/tmp/interference_core3.out 2>/tmp/interference_core3.err &

echo $! > {INTERFERENCE_REMOTE_PID}

sleep 0.5

if kill -0 $(cat {INTERFERENCE_REMOTE_PID}) 2>/dev/null; then
    echo "started pid=$(cat {INTERFERENCE_REMOTE_PID})"
else
    echo "failed to start"
    exit 1
fi
"""

    rc, stdout, stderr = run(ssh_cmd(MEMCACHED_IP, create_and_run_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to start interference job: {stderr}")
        return False

    logger.info(f"  ✓ Interference job: {stdout}")
    return True


# ─────────────────────────────────────────────
# CPU LOGGER
# ─────────────────────────────────────────────

def start_cpu_logger(run_label: str, logger: logging.Logger, dry_run: bool) -> bool:
    cpu_remote_file = f"~/cpu_{run_label}.txt"
    cpu_pid_file = f"~/cpu_{run_label}.pid"
    cpu_remote_script = f"~/cpu_logger_{run_label}.sh"

    cpu_logger_script = """#!/usr/bin/env bash
set -u
OUT="$1"
: > "$OUT"

while true; do
    epoch_ms=$(date +%s%3N)
    iso_ts=$(date --iso-8601=milliseconds)
    host=$(hostname)

    echo "# sample epoch_ms=${epoch_ms} iso_ts=${iso_ts} host=${host}" >> "$OUT"
    grep -E '^cpu[0-9]+ ' /proc/stat >> "$OUT"
    echo >> "$OUT"

    sleep 0.2
done
"""

    cmd = f"""
cat > {cpu_remote_script} <<'EOF'
{cpu_logger_script}
EOF

chmod +x {cpu_remote_script}

nohup bash {cpu_remote_script} {cpu_remote_file} \\
    >/tmp/cpu_logger_{run_label}.out 2>&1 &

echo $! > {cpu_pid_file}
"""

    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to start CPU logger: {stderr}")
        return False

    if not dry_run:
        time.sleep(1)

    return True


def stop_cpu_logger(run_label: str, logger: logging.Logger, dry_run: bool) -> None:
    cpu_pid_file = f"~/cpu_{run_label}.pid"

    cmd = (
        f"if [ -f {cpu_pid_file} ]; then "
        f"kill $(cat {cpu_pid_file}) 2>/dev/null || true; "
        f"rm -f {cpu_pid_file}; "
        f"fi"
    )

    run(ssh_cmd(MEMCACHED_IP, cmd), logger, dry_run)


def fetch_cpu_log(run_label: str, result_dir: str, logger: logging.Logger, dry_run: bool) -> bool:
    key_path = os.path.expanduser(SSH_KEY)

    remote_file = f"~/cpu_{run_label}.txt"
    local_file = os.path.join(result_dir, f"{run_label}_cpu.txt")

    cmd = (
        f"scp -i {shlex.quote(key_path)} {SSH_OPTS} "
        f"ubuntu@{MEMCACHED_IP}:{shlex.quote(remote_file)} "
        f"{shlex.quote(local_file)}"
    )

    rc, _, stderr = run(cmd, logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to fetch CPU log: {stderr}")
        return False

    logger.info(f"  ✓ CPU log saved → {local_file}")
    return True


# ─────────────────────────────────────────────
# MCPERF
# ─────────────────────────────────────────────

def load_memcached(logger: logging.Logger, dry_run: bool) -> bool:
    load_cmd = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {INTERNAL_MEMCACHED_IP} --loadonly"
    )

    logger.info("  → Loading memcached")
    rc, _, stderr = run(ssh_cmd(MEASURE_IP, load_cmd), logger, dry_run)

    if rc != 0:
        logger.error(f"  ✗ Load failed: {stderr}")
        return False

    return True


def run_mcperf(
    run_label: str,
    result_dir: str,
    logger: logging.Logger,
    dry_run: bool,
) -> bool:
    os.makedirs(result_dir, exist_ok=True)

    out_file = os.path.join(result_dir, f"{run_label}.txt")

    bench_cmd = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {INTERNAL_MEMCACHED_IP} -a {INTERNAL_AGENT_IP} "
        f"--noload {MCPERF_PARAMS}"
    )

    if not load_memcached(logger, dry_run):
        return False

    if not start_cpu_logger(run_label, logger, dry_run):
        return False

    logger.info(f"  → Running mcperf: {run_label}")
    rc, stdout, stderr = run(ssh_cmd(MEASURE_IP, bench_cmd), logger, dry_run)

    stop_cpu_logger(run_label, logger, dry_run)

    if rc != 0:
        logger.error(f"  ✗ mcperf failed: {stderr}")
        return False

    with open(out_file, "w") as f:
        f.write(f"# Run: {run_label}\n")
        f.write(f"# Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"# MCPERF_PARAMS: {MCPERF_PARAMS}\n\n")
        f.write(stdout)

    logger.info(f"  ✓ mcperf output saved → {out_file}")

    return fetch_cpu_log(run_label, result_dir, logger, dry_run)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = f"logs_interference/{timestamp}"
    result_dir = f"results_interference/{timestamp}"

    logger, log_file = setup_logging(log_dir)

    logger.info("=" * 70)
    logger.info("Memcached Interference Benchmark")
    logger.info(f"Threads: {THREAD_CONFIGS}")
    logger.info(f"Cores: {CORE_CONFIGS}")
    logger.info(f"Interference modes: {INTERFERENCE_MODES}")
    logger.info(f"Repetitions: {args.repetitions}")
    logger.info(f"Results dir: {result_dir}")
    logger.info("=" * 70)

    success = 0
    failure = 0

    try:
        for threads, cores in product(THREAD_CONFIGS, CORE_CONFIGS):
            for interference in INTERFERENCE_MODES:
                # Do not run interference on core 3 if memcached itself uses core 3.
                if INTERFERENCE_CORE in cores and interference != "none":
                    logger.info(
                        f"Skipping cores={cores}, interference={interference}: "
                        f"core {INTERFERENCE_CORE} belongs to memcached"
                    )
                    continue

                cores_label = "_".join(str(c) for c in cores)
                config_label = f"t{threads}_c{cores_label}_int_{interference}"

                logger.info("")
                logger.info("─" * 70)
                logger.info(f"CONFIG: {config_label}")
                logger.info("─" * 70)

                ok = set_threads_and_cores(threads, cores, logger, args.dry_run)
                if not ok:
                    failure += args.repetitions
                    continue

                for rep in range(1, args.repetitions + 1):
                    run_label = f"{config_label}_rep{rep}"
                    logger.info(f"  → Repetition {rep}/{args.repetitions}: {run_label}")

                    if not start_interference(interference, logger, args.dry_run):
                        failure += 1
                        continue

                    ok = run_mcperf(run_label, result_dir, logger, args.dry_run)

                    stop_interference(logger, args.dry_run)

                    if ok:
                        success += 1
                    else:
                        failure += 1

                    if rep < args.repetitions:
                        logger.info(f"  → Sleeping {SLEEP_BETWEEN_REPS}s")
                        if not args.dry_run:
                            time.sleep(SLEEP_BETWEEN_REPS)

    finally:
        stop_interference(logger, args.dry_run)

    logger.info("")
    logger.info("=" * 70)
    logger.info("DONE")
    logger.info(f"Successful runs: {success}")
    logger.info(f"Failed runs: {failure}")
    logger.info(f"Results dir: {result_dir}")
    logger.info(f"Log file: {log_file}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()