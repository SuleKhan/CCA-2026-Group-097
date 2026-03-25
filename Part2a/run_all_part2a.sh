#!/usr/bin/env bash
set -euo pipefail

WORKLOADS=(barnes blackscholes canneal freqmine radix streamcluster vips)
INTERFS=(none cpu l1d l1i l2 llc membw)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
YAML_DIR="$ROOT_DIR/parsec-benchmarks/part2a"
INT_DIR="$ROOT_DIR/interference"
OUT_DIR="$SCRIPT_DIR"
TMP_DIR="$OUT_DIR/tmp"
CSV_FILE="$OUT_DIR/part2a_times.csv"

mkdir -p "$OUT_DIR" "$TMP_DIR"
echo "workload,interference,real_seconds,log_file" > "$CSV_FILE"

get_time() {
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1], "r", encoding="utf-8").read()
m = re.search(r"real\s+(\d+)m([\d.]+)s", text)
if not m:
    raise SystemExit(1)
print(f"{int(m.group(1))*60 + float(m.group(2)):.3f}")
PY
}

cleanup() {
  kubectl delete jobs --all --ignore-not-found=true >/dev/null 2>&1 || true
  kubectl delete pods --all --ignore-not-found=true >/dev/null 2>&1 || true
  sleep 2
}

kubectl get nodes >/dev/null

for w in "${WORKLOADS[@]}"; do
  job_yaml="$YAML_DIR/parsec-$w.yaml"
  job_name="parsec-$w"

  [[ -f "$job_yaml" ]] || { echo "Missing $job_yaml"; exit 1; }

  for i in "${INTERFS[@]}"; do
    log_file="$OUT_DIR/${job_name}-${i}.log"

    cleanup

    if [[ "$i" != "none" ]]; then
      kubectl create -f "$INT_DIR/ibench-$i.yaml"
      sleep 5
    fi

    kubectl create -f "$job_yaml"
    kubectl wait --for=condition=Complete "job/$job_name" --timeout=30m

    pod_name="$(kubectl get pods --selector=job-name=${job_name} --output=jsonpath='{.items[0].metadata.name}')"
    kubectl logs "$pod_name" > "$log_file"

    t="$(get_time "$log_file")"
    echo "$w,$i,$t,$log_file" >> "$CSV_FILE"

    cleanup
  done
done