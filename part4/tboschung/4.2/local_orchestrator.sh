#!/usr/bin/env bash

set -euo pipefail

# ====== CONFIG ======
SSH_KEY="~/.ssh/cloud-computing"

MEASURE_IP="34.52.145.56"
MEMCACHED_IP="34.78.123.59"

INTERNAL_MEMCACHED_IP="10.0.16.5"
INTERNAL_AGENT_IP="10.0.16.6"

LOG_DIR="/Users/Tobias/Documents/coding_projects/CCA-2026-Group-097/part4/tboschung/4.2/logs"
mkdir -p "$LOG_DIR"

log() {
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] $1"
}

# ====== EXPERIMENT LOOP ======
for i in {1..3}; do
  RUN_TS=$(date +"%Y%m%d_%H%M%S")
  log "===== START RUN $i ====="

  # -------------------------
  # 1. START MC PERF (ASYNC)
  # -------------------------
  log "Starting mcperf (async)"

  ssh -i "$SSH_KEY" ubuntu@"$MEASURE_IP" "
    nohup ./memcache-perf-dynamic/mcperf \
      -s $INTERNAL_MEMCACHED_IP \
      -a $INTERNAL_AGENT_IP \
      --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1800 \
      --qps_interval 15 --qps_min 5000 --qps_max 110000 \
      --qps_seed 2345 \
      > mcperf_run_${i}.log 2>&1 &
    echo \$!
  " > "$LOG_DIR/mcperf_pid_${i}.txt"

  MC_PID=$(cat "$LOG_DIR/mcperf_pid_${i}.txt")
  log "mcperf PID: $MC_PID"

  # -------------------------
  # 2. START SCHEDULER (ASYNC)
  # -------------------------
  log "Starting dynamic scheduler (async)"

  ssh -i "$SSH_KEY" ubuntu@"$MEMCACHED_IP" "
    nohup python3 dynamic_scheduler.py \
      > scheduler_run_${i}.log 2>&1 &
    echo \$!
  " > "$LOG_DIR/scheduler_pid_${i}.txt"

  SCH_PID=$(cat "$LOG_DIR/scheduler_pid_${i}.txt")
  log "scheduler PID: $SCH_PID"

  # -------------------------
  # 3. WAIT FOR MC PERF TO FINISH
  # -------------------------
  log "Waiting for mcperf to finish..."

  while ssh -i "$SSH_KEY" ubuntu@"$MEASURE_IP" "kill -0 $MC_PID 2>/dev/null"; do
    sleep 10
  done

  log "mcperf finished"

  # give scheduler a bit more time to flush logs
  sleep 10

  # -------------------------
  # 4. COLLECT LOGS
  # -------------------------
  log "Collecting logs"

  scp -i "$SSH_KEY" ubuntu@"$MEMCACHED_IP":cpu.log \
    "$LOG_DIR/cpu_run${i}_${RUN_TS}.log"

  scp -i "$SSH_KEY" ubuntu@"$MEMCACHED_IP":log20260503_182604.txt \
    "$LOG_DIR/app_run${i}_${RUN_TS}.log"

  scp -i "$SSH_KEY" ubuntu@"$MEASURE_IP":mcperf_run_${i}.log \
    "$LOG_DIR/mcperf_run${i}_${RUN_TS}.log"

  scp -i "$SSH_KEY" ubuntu@"$MEMCACHED_IP":scheduler_run_${i}.log \
    "$LOG_DIR/scheduler_run${i}_${RUN_TS}.log"

  log "Run $i complete"
done

log "ALL RUNS FINISHED"