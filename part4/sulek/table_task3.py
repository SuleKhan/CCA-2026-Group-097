#!/usr/bin/env python3

# python table_task3.py "/Users/Tobias/Documents/coding_projects/CCA-2026-Group-097/part4/sulek/final_scheduler/Task 4.3/Run 1/jobs_log.txt" "/Users/Tobias/Documents/coding_projects/CCA-2026-Group-097/part4/sulek/final_scheduler/Task 4.3/Run 2/jobs_log.txt" "/Users/Tobias/Documents/coding_projects/CCA-2026-Group-097/part4/sulek/final_scheduler/Task 4.3/Run 3/jobs_log.txt"

import argparse
import csv
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path


LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+(?P<action>start|end|update_cores)\s+(?P<job>\S+)"
)


DEFAULT_IGNORE = {"scheduler", "memcached"}


def parse_log(path: Path, run_id: int, ignore_jobs: set[str]) -> list[dict]:
    starts: dict[str, datetime] = {}
    rows: list[dict] = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            m = LINE_RE.match(line)
            if not m:
                continue

            ts = datetime.fromisoformat(m.group("ts"))
            action = m.group("action")
            job = m.group("job")

            if job in ignore_jobs:
                continue

            if action == "start":
                starts[job] = ts

            elif action == "end":
                if job not in starts:
                    print(f"warning: {path}:{line_no}: end without start for {job}")
                    continue

                start_ts = starts.pop(job)
                duration_s = (ts - start_ts).total_seconds()

                rows.append(
                    {
                        "run": run_id,
                        "log_file": str(path),
                        "job": job,
                        "start": start_ts.isoformat(),
                        "end": ts.isoformat(),
                        "duration_s": duration_s,
                    }
                )

    for job, start_ts in starts.items():
        print(f"warning: {path}: start without end for {job} at {start_ts}")

    return rows


def summarize(rows: list[dict]) -> list[dict]:
    durations_by_job = defaultdict(list)

    for row in rows:
        durations_by_job[row["job"]].append(float(row["duration_s"]))

    summary = []

    for job in sorted(durations_by_job):
        values = durations_by_job[job]

        avg = statistics.mean(values)
        std = statistics.stdev(values) if len(values) >= 2 else 0.0

        summary.append(
            {
                "job": job,
                "n": len(values),
                "avg_duration_s": avg,
                "std_duration_s": std,
            }
        )

    return summary


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse scheduler logs and report job execution-time statistics."
    )
    parser.add_argument(
        "logs",
        nargs=3,
        type=Path,
        help="Exactly 3 log files to parse",
    )
    parser.add_argument(
        "--jobs-out",
        type=Path,
        default=Path("execution_jobs.csv"),
        help="CSV output for individual job executions",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("execution_summary.csv"),
        help="CSV output for avg/std summary",
    )
    parser.add_argument(
        "--include-infra",
        action="store_true",
        help="Include scheduler and memcached in the results",
    )

    args = parser.parse_args()

    ignore_jobs = set() if args.include_infra else DEFAULT_IGNORE

    all_rows = []
    for run_id, log_path in enumerate(args.logs, start=1):
        all_rows.extend(parse_log(log_path, run_id, ignore_jobs))

    summary = summarize(all_rows)

    write_csv(
        args.jobs_out,
        all_rows,
        ["run", "log_file", "job", "start", "end", "duration_s"],
    )

    write_csv(
        args.summary_out,
        summary,
        ["job", "n", "avg_duration_s", "std_duration_s"],
    )

    print("\nExecution summary")
    print("-----------------")
    print(f"{'job':<16} {'n':>3} {'avg_s':>12} {'std_s':>12}")

    for row in summary:
        print(
            f"{row['job']:<16} "
            f"{row['n']:>3} "
            f"{row['avg_duration_s']:>12.6f} "
            f"{row['std_duration_s']:>12.6f}"
        )

    print(f"\nWrote {args.jobs_out}")
    print(f"Wrote {args.summary_out}")


if __name__ == "__main__":
    main()