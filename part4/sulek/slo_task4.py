import re
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

# BASE_DIR = Path(
#     "/Users/Tobias/Documents/coding_projects/"
#     "CCA-2026-Group-097/part4/sulek/final_scheduler/"
#     "Task 4.4/QPS_6"
# )

BASE_DIR = Path(
    "final_scheduler/Task 4.4 FIXED/QPS_7"
)

RUNS = ["Run 1", "Run 2", "Run 3"]
# RUNS = ["Run 1"]

INTERVAL_SECONDS = 5

# Question asks for p95 latency > 0.8 ms
SLO_THRESHOLD_MS = 800

BATCH_JOBS = {
    "blackscholes",
    "canneal",
    "freqmine",
    "radix",
    "streamcluster",
    "vips",
    "barnes",
}


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def parse_iso_utc(ts: str) -> datetime:
    """
    Parse timestamps from jobs_log.txt and force UTC timezone.
    """
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def parse_measure_log(measure_log_path: Path) -> pd.DataFrame:
    """
    Parse memcached measurement log.

    Returns a DataFrame with:
        time, qps, p95_ms
    """

    with open(measure_log_path, "r", encoding="utf-8", errors="ignore") as f:
        measure_log = f.read()

    start_ts = None

    for line in measure_log.splitlines():
        if "Timestamp start" in line:
            start_ts = int(line.split(":")[1].strip())

    if start_ts is None:
        raise ValueError(f"Missing Timestamp start in {measure_log_path}")

    rows = []
    interval_idx = 0

    for line in measure_log.splitlines():
        if not line.strip().startswith("read"):
            continue

        nums = re.findall(r"[\d.]+", line)

        if len(nums) < 12:
            continue

        # Same indexing convention as your plotting script:
        # qps = nums[-2]
        # p95 = nums[-6]
        qps = float(nums[-2])
        p95_ms = float(nums[-6])

        t = datetime.fromtimestamp(
            (start_ts + interval_idx * INTERVAL_SECONDS * 1000) / 1000,
            tz=timezone.utc,
        )

        rows.append(
            {
                "time": t,
                "qps": qps,
                "p95_ms": p95_ms,
            }
        )

        interval_idx += 1

    return pd.DataFrame(rows)


def parse_batch_window(jobs_log_path: Path):
    """
    Find the interval from:
        first batch job start
    to:
        last batch job end
    """

    with open(jobs_log_path, "r", encoding="utf-8", errors="ignore") as f:
        jobs_log = f.read()

    job_start_re = re.compile(
        r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+start\s+(\w+)\s+\[([^\]]+)\]\s+(\d+)"
    )

    job_end_re = re.compile(
        r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+end\s+(\w+)"
    )

    batch_starts = []
    batch_ends = []

    for line in jobs_log.splitlines():
        m = job_start_re.search(line)
        if m:
            ts, job, _, _ = m.groups()
            if job in BATCH_JOBS:
                batch_starts.append(parse_iso_utc(ts))
            continue

        m = job_end_re.search(line)
        if m:
            ts, job = m.groups()
            if job in BATCH_JOBS:
                batch_ends.append(parse_iso_utc(ts))

    if not batch_starts:
        raise ValueError(f"No batch job starts found in {jobs_log_path}")

    if not batch_ends:
        raise ValueError(f"No batch job ends found in {jobs_log_path}")

    return min(batch_starts), max(batch_ends)


def calculate_slo_violation_ratio(run_dir: Path):
    """
    Calculate SLO violation ratio for one run.
    """

    measure_log_path = run_dir / "measure_log.txt"
    jobs_log_path = run_dir / "jobs_log.txt"

    df = parse_measure_log(measure_log_path)

    if df.empty:
        raise ValueError(f"No memcached datapoints found in {measure_log_path}")

    batch_start, batch_end = parse_batch_window(jobs_log_path)

    # Count only datapoints during batch-job execution window
    window_df = df[
        (df["time"] >= batch_start)
        & (df["time"] <= batch_end)
    ].copy()

    total_datapoints = len(window_df)
    violation_datapoints = int(
        (window_df["p95_ms"] > SLO_THRESHOLD_MS).sum()
    )

    ratio = (
        violation_datapoints / total_datapoints
        if total_datapoints > 0
        else float("nan")
    )

    return {
        "run": run_dir.name,
        "batch_start": batch_start,
        "batch_end": batch_end,
        "total_datapoints": total_datapoints,
        "violation_datapoints": violation_datapoints,
        "slo_threshold_ms": SLO_THRESHOLD_MS,
        "slo_violation_ratio": ratio,
    }


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    results = []

    for run_name in RUNS:
        run_dir = BASE_DIR / run_name
        result = calculate_slo_violation_ratio(run_dir)
        results.append(result)

    results_df = pd.DataFrame(results)

    print("\nSLO violation ratio per run")
    print(results_df.to_string(index=False))

    total_violations = results_df["violation_datapoints"].sum()
    total_datapoints = results_df["total_datapoints"].sum()

    overall_ratio = (
        total_violations / total_datapoints
        if total_datapoints > 0
        else float("nan")
    )

    print("\nOverall across all runs")
    print(f"Violation datapoints: {total_violations}")
    print(f"Total datapoints:     {total_datapoints}")
    print(f"SLO violation ratio:  {overall_ratio:.6f}")


if __name__ == "__main__":
    main()