#!/bin/bash

# DONT FORGET TO DO THIS!!!! manual setup with reverse tunnel
# ssh -R 2222:localhost:22 -i ~/.ssh/cloud-computing ubuntu@34.38.233.180
# ssh-keygen -t ed25519
# ssh-copy-id -p 2222 Tobias@localhost
# ssh -p 2222 Tobias@localhost "echo hello"

# run agent in ssh
# cd ./memcache-perf && ./mcperf -T 8 -A

# ====== CONFIG ======
REMOTE_USER="Tobias"
REMOTE_HOST="localhost"
REMOTE_PORT="2222"
REMOTE_DIR="Documents/coding_projects/cca_project/cloud-comp-arch-project"

MEMCACHED_IP=100.96.4.2
INTERNAL_AGENT_IP=10.0.16.3
ENV_NAME="part1"

IBENCH_TYPES=("cpu" "l1d" "l1i" "l2" "llc" "membw")
RUNS=3

cd ./memcache-perf

# ====== WAIT FOR POD READY ======
wait_for_ibench_ready() {
  local type=$1
  local timeout=120
  local elapsed=0

  echo "Waiting for ibench-${type} pod to be 1/1 Ready..."
  while true; do
    STATUS=$(ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST} \
      "/usr/local/bin/kubectl get pods --no-headers 2>/dev/null | grep ibench-${type} | awk '{print \$2}'")

    if [ "$STATUS" = "1/1" ]; then
      echo "ibench-${type} is Ready."
      return 0
    fi

    if [ "$elapsed" -ge "$timeout" ]; then
      echo "Timeout waiting for ibench-${type} to be ready!" >&2
      return 1
    fi

    sleep 3
    elapsed=$((elapsed + 3))
  done
}

# ====== WAIT FOR POD DELETED ======
wait_for_ibench_deleted() {
  local type=$1
  local timeout=120
  local elapsed=0

  echo "Waiting for ibench-${type} pod to be fully deleted..."
  while true; do
    STATUS=$(ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST} \
      "/usr/local/bin/kubectl get pods --no-headers 2>/dev/null | grep ibench-${type}")

    if [ -z "$STATUS" ]; then
      echo "ibench-${type} pod deleted."
      return 0
    fi

    if [ "$elapsed" -ge "$timeout" ]; then
      echo "Timeout waiting for ibench-${type} to be deleted!" >&2
      return 1
    fi

    sleep 3
    elapsed=$((elapsed + 3))
  done
}

run_mcperf() {
  local label=$1
  local outfile="${ENV_NAME}_${label}.txt"

  ./mcperf -s "$MEMCACHED_IP" -a "$INTERNAL_AGENT_IP" \
    --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 -w 2 \
    --scan 5000:80000:5000 \
    > "$outfile" 2>&1

  cat "$outfile"

  scp -P $REMOTE_PORT "$outfile" ${REMOTE_USER}@${REMOTE_HOST}:~/${REMOTE_DIR}/logs
}

# ====== BASELINE ======
echo "=============================="
echo "Running baseline (no interference)"
echo "=============================="

for i in $(seq 1 $RUNS); do
  echo "Baseline run $i starting..."
  run_mcperf "baseline_mcperf_run${i}"
  echo "Baseline run $i finished."
done

# ====== INTERFERENCE RUNS ======
for IBENCH_TYPE in "${IBENCH_TYPES[@]}"; do
  echo "=============================="
  echo "Running ibench-$IBENCH_TYPE"
  echo "=============================="

  for i in $(seq 1 $RUNS); do
    echo "Run $i starting..."

    ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST} \
      "/usr/local/bin/kubectl create -f ${REMOTE_DIR}/interference/ibench-${IBENCH_TYPE}.yaml" 2>&1

    wait_for_ibench_ready "$IBENCH_TYPE" || exit 1

    run_mcperf "${IBENCH_TYPE}_mcperf_run${i}"

    echo "Run $i finished."

    ssh -p $REMOTE_PORT ${REMOTE_USER}@${REMOTE_HOST} \
      "/usr/local/bin/kubectl delete --wait=true -f ${REMOTE_DIR}/interference/ibench-${IBENCH_TYPE}.yaml" > /dev/null 2>&1

    wait_for_ibench_deleted "$IBENCH_TYPE" || exit 1
  done
done