#!/usr/bin/env python3
"""
Memcached Benchmark Automation Script
Loops over thread and core configurations, runs each N times for consistency,
and logs all output to structured log files.

Usage:
    python3 run_benchmark.py [--repetitions N] [--dry-run]

Edit the CONFIG section below before running.
"""

import subprocess
import time
import os
import argparse
import logging
import shlex
from datetime import datetime
from itertools import product

# ─────────────────────────────────────────────
# CONFIG — edit these before running
# ─────────────────────────────────────────────

SSH_KEY             = "~/.ssh/cloud-computing"
SSH_OPTS            = "-o StrictHostKeyChecking=no"

MEMCACHED_IP        = "35.241.183.126"
MEASURE_IP          = "34.53.217.115"

INTERNAL_MEMCACHED_IP = "10.0.16.4"
INTERNAL_AGENT_IP     = "10.0.16.3"

# Thread counts to sweep
THREAD_CONFIGS = [3]

# Core pinning configs (each entry is a list of core IDs)
CORE_CONFIGS = [
    [0],
    [0, 1],
    [0, 1, 2],
]

# How many times to repeat each (threads, cores) combination
REPETITIONS = 3

# mcperf benchmark parameters (adjust as needed)
MCPERF_PARAMS = "-T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 --scan 5000:125000:10000"

# Seconds to wait between repetitions (reduces thermal/load noise)
SLEEP_BETWEEN_REPS = 5

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────

def setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"benchmark_{timestamp}.log")

    logger = logging.getLogger("benchmark")
    logger.setLevel(logging.DEBUG)

    # File handler — full detail
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    # Console handler — info and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Logging to: {log_file}")
    return logger, log_file


# ─────────────────────────────────────────────
# SSH HELPERS
# ─────────────────────────────────────────────

def ssh_cmd(host: str, remote_cmd: str, key: str = SSH_KEY, opts: str = SSH_OPTS) -> str:
    """Build an ssh command while safely quoting the remote command."""
    key_path = os.path.expanduser(key)
    return f"ssh -i {shlex.quote(key_path)} {opts} ubuntu@{host} {shlex.quote(remote_cmd)}"


def run(cmd: str, logger: logging.Logger, dry_run: bool = False) -> tuple[int, str, str]:
    """
    Execute a shell command.
    Returns (returncode, stdout, stderr).
    """
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
        logger.error(f"  ✗ Command exited with code {result.returncode}: {cmd}")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ─────────────────────────────────────────────
# BENCHMARK STEPS
# ─────────────────────────────────────────────

def set_threads_and_cores(
    threads: int,
    cores: list[int],
    logger: logging.Logger,
    dry_run: bool,
) -> bool:
    """
    Configure memcached with:
    - Correct thread count (-t)
    - Enforced CPU affinity via systemd AllowedCPUs
    - Verified affinity after restart
    """
    cores_str = ",".join(str(c) for c in cores)

    # Step 1: update memcached thread config
    update_threads_cmd = (
        f"sudo sed -i -E \"s/^-t .*/-t {threads}/\" /etc/memcached.conf"
    )
    logger.info(f"  → Setting threads={threads} in config...")
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, update_threads_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to update thread config: {stderr}")
        return False

    # Step 2: set CPU affinity at systemd level (PERSISTENT + RELIABLE)
    set_cpu_cmd = f"sudo systemctl set-property memcached AllowedCPUs={cores_str}"
    logger.info(f"  → Setting AllowedCPUs={cores_str}...")
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, set_cpu_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to set AllowedCPUs: {stderr}")
        return False

    # Step 3: restart memcached cleanly
    restart_cmd = "sudo systemctl restart memcached"
    logger.info("  → Restarting memcached...")
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, restart_cmd), logger, dry_run)
    if rc != 0:
        logger.warning(f"  ⚠ Restart returned non-zero (may be OK): {stderr}")

    # Step 4: wait for service
    wait_secs = 4
    logger.info(f"  → Waiting {wait_secs}s for memcached to start...")
    if not dry_run:
        time.sleep(wait_secs)

    # Step 5: VERIFY affinity (this is critical)
    verify_cmd = "taskset -cp $(pgrep -o memcached)"
    logger.info("  → Verifying CPU affinity...")
    rc, stdout, stderr = run(ssh_cmd(MEMCACHED_IP, verify_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to verify affinity: {stderr}")
        return False

    logger.info(f"  ✓ Affinity check: {stdout}")

    target = "0-2" if cores_str == "0,1,2" else cores_str
    if target not in stdout:
        logger.error(f"  ✗ Affinity mismatch! Expected cores {cores_str}, got: {stdout}")
        return False

    logger.info(f"  ✓ memcached configured — threads={threads}, cores=[{cores_str}]")
    return True


def run_mcperf(
    run_label: str,
    result_dir: str,
    logger: logging.Logger,
    dry_run: bool,
) -> bool:
    """
    Load data into memcached, then run the benchmark.
    Output is saved to a per-run file in result_dir.
    Returns True on success.
    """
    os.makedirs(result_dir, exist_ok=True)
    out_file = os.path.join(result_dir, f"{run_label}.txt")

    load_cmd_remote = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {INTERNAL_MEMCACHED_IP} --loadonly"
    )
    bench_cmd_remote = (
        f"cd ~/memcache-perf-dynamic && "
        f"./mcperf -s {INTERNAL_MEMCACHED_IP} -a {INTERNAL_AGENT_IP} "
        f"--noload {MCPERF_PARAMS}"
    )

    # Step 1: load
    logger.info(f"  → Loading data into memcached...")
    rc, stdout, stderr = run(ssh_cmd(MEASURE_IP, load_cmd_remote), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Load step failed: {stderr}")
        logger.error(f"  ✗ Failed command: {ssh_cmd(MEASURE_IP, load_cmd_remote)}")
        return False
    
    # Step 2: benchmark WITH CPU monitoring
    logger.info(f"  → Running benchmark ({run_label}) with raw /proc/stat CPU scraping...")

    # Start raw CPU logging on the MEMCACHED server.
    # Each sample has an absolute epoch-ms timestamp followed by raw /proc/stat
    # cpuN lines. The plotter computes utilization from counter deltas, so this
    # avoids fragile mpstat parsing and locale/AM-PM output issues.
    cpu_remote_file = f"~/cpu_{run_label}.txt"
    cpu_pid_file = f"~/cpu_{run_label}.pid"
    cpu_remote_script = f"~/cpu_logger_{run_label}.sh"

    cpu_logger_script = """#!/usr/bin/env bash
set -u
OUT=\"$1\"
: > \"$OUT\"
while true; do
epoch_ms=$(date +%s%3N)
iso_ts=$(date --iso-8601=milliseconds)
host=$(hostname)
echo \"# sample epoch_ms=${epoch_ms} iso_ts=${iso_ts} host=${host}\" >> \"$OUT\"
grep -E '^cpu[0-9]+ ' /proc/stat >> \"$OUT\"
echo >> \"$OUT\"
sleep 0.2
done
"""

    create_cpu_logger_cmd = (
        f"cat > {cpu_remote_script} <<'CPU_LOGGER_EOF'\n"
        f"{cpu_logger_script}"
        f"CPU_LOGGER_EOF\n"
        f"chmod +x {cpu_remote_script}\n"
        f"nohup bash {cpu_remote_script} {cpu_remote_file} >/tmp/cpu_logger_{run_label}.out 2>&1 &\n"
        f"echo $! > {cpu_pid_file}\n"
    )
    rc, _, stderr = run(ssh_cmd(MEMCACHED_IP, create_cpu_logger_cmd), logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to start CPU logger: {stderr}")
        return False

    # Give the logger a moment to write the first sample before mcperf starts.
    if not dry_run:
        time.sleep(1)

    # Run benchmark from MEASURE node.
    rc, stdout, stderr = run(ssh_cmd(MEASURE_IP, bench_cmd_remote), logger, dry_run)

    # Stop CPU logging even if the benchmark fails.
    stop_cpu_cmd = (
        f"if [ -f {cpu_pid_file} ]; then "
        f"kill $(cat {cpu_pid_file}) 2>/dev/null || true; "
        f"rm -f {cpu_pid_file}; "
        f"fi"
    )
    run(ssh_cmd(MEMCACHED_IP, stop_cpu_cmd), logger, dry_run)

    if rc != 0:
        logger.error(f"  ✗ Benchmark failed: {stderr}")
        return False

    # Save raw mcperf output.
    with open(out_file, "w") as f:
        f.write(f"# Run: {run_label}\n")
        f.write(f"# Timestamp: {datetime.now().isoformat()}\n\n")
        f.write(stdout)
    logger.info(f"  ✓ Results saved → {out_file}")

    # Fetch raw CPU scrape. This must match cpu_remote_file above.
    key_path = os.path.expanduser(SSH_KEY)
    local_cpu_file = os.path.join(result_dir, f"{run_label}_cpu.txt")
    cpu_fetch_cmd = (
        f"scp -i {shlex.quote(key_path)} {SSH_OPTS} "
        f"ubuntu@{MEMCACHED_IP}:{shlex.quote(cpu_remote_file)} "
        f"{shlex.quote(local_cpu_file)}"
    )
    rc, _, stderr = run(cpu_fetch_cmd, logger, dry_run)
    if rc != 0:
        logger.error(f"  ✗ Failed to fetch CPU log: {stderr}")
        return False

    logger.info(f"  ✓ CPU scrape saved → {local_cpu_file}")

    return True

# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Memcached benchmark automation")
    parser.add_argument(
        "--repetitions", type=int, default=REPETITIONS,
        help=f"Repetitions per config (default: {REPETITIONS})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands without executing them"
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir    = f"logs_b/{timestamp}"
    result_dir = f"results_b/{timestamp}"

    logger, log_file = setup_logging(log_dir)

    total_configs = len(THREAD_CONFIGS) * len(CORE_CONFIGS)
    total_runs    = total_configs * args.repetitions

    logger.info("=" * 60)
    logger.info("Memcached Benchmark Automation")
    logger.info(f"  Thread configs : {THREAD_CONFIGS}")
    logger.info(f"  Core configs   : {CORE_CONFIGS}")
    logger.info(f"  Repetitions    : {args.repetitions}")
    logger.info(f"  Total runs     : {total_runs}")
    logger.info(f"  Dry run        : {args.dry_run}")
    logger.info("=" * 60)

    success_count = 0
    failure_count = 0
    run_index     = 0

    for threads, cores in product(THREAD_CONFIGS, CORE_CONFIGS):
        cores_label = "_".join(str(c) for c in cores)
        config_label = f"t{threads}_c{cores_label}"

        logger.info("")
        logger.info(f"{'─'*60}")
        logger.info(f"CONFIG: threads={threads}, cores={cores}  [{config_label}]")
        logger.info(f"{'─'*60}")

        # Configure memcached once per (threads, cores) pair
        ok = set_threads_and_cores(threads, cores, logger, args.dry_run)
        if not ok:
            logger.error(f"Skipping all repetitions for {config_label} due to config failure.")
            failure_count += args.repetitions
            continue

        for rep in range(1, args.repetitions + 1):
            run_index += 1
            run_label = f"{config_label}_rep{rep}"
            logger.info(f"  [{run_index}/{total_runs}] Repetition {rep}/{args.repetitions} — {run_label}")

            ok = run_mcperf(run_label, result_dir, logger, args.dry_run)
            if ok:
                success_count += 1
            else:
                failure_count += 1

            if rep < args.repetitions:
                logger.info(f"  Sleeping {SLEEP_BETWEEN_REPS}s before next repetition...")
                if not args.dry_run:
                    time.sleep(SLEEP_BETWEEN_REPS)

    logger.info("")
    logger.info("=" * 60)
    logger.info("DONE")
    logger.info(f"  Successful runs : {success_count}/{total_runs}")
    logger.info(f"  Failed runs     : {failure_count}/{total_runs}")
    logger.info(f"  Results dir     : {result_dir}/")
    logger.info(f"  Log file        : {log_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()