#!/usr/bin/env python3
"""
Create memcached performance plots using raw /proc/stat CPU scrape files.

Expected files in a result directory:
  t3_c0_rep1.txt
  t3_c0_rep1_cpu.txt
  t3_c0_1_rep2.txt
  t3_c0_1_rep2_cpu.txt
  t3_c0_1_2_rep3.txt
  t3_c0_1_2_rep3_cpu.txt

CPU file format produced by measurements_1b_rawcpu.py:
  # sample epoch_ms=1777828232000 iso_ts=... host=...
  cpu0  123 0 456 789 ...
  cpu1  ...

Usage:
  python3 plot_memcached_rawcpu.py results_b/20260506_123456
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import pandas as pd

MC_COLUMNS = [
    "type", "avg", "std", "min", "p5", "p10", "p50", "p67", "p75", "p80",
    "p85", "p90", "p95", "p99", "p999", "p9999", "QPS", "target",
    "ts_start", "ts_end",
]

RUN_RE = re.compile(r"^t(?P<threads>\d+)_c(?P<cores>[0-9_]+)_rep(?P<rep>\d+)$")
SAMPLE_RE = re.compile(r"epoch_ms=(?P<epoch_ms>\d+)")


def parse_run_label(path: Path) -> tuple[str, int, list[int], int]:
    label = path.stem
    m = RUN_RE.match(label)
    if not m:
        raise ValueError(f"Cannot parse run label from {path.name}; expected t3_c0_1_rep2.txt style")
    core_ids = [int(x) for x in m.group("cores").split("_")]
    return label, len(core_ids), core_ids, int(m.group("rep"))


def read_mcperf(path: Path) -> pd.DataFrame:
    rows: list[list[str]] = []
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("Warning!") or line.startswith("CPU Usage"):
                continue
            parts = line.split()
            if parts and parts[0] == "read" and len(parts) >= len(MC_COLUMNS):
                rows.append(parts[: len(MC_COLUMNS)])

    if not rows:
        raise ValueError(f"No mcperf read rows found in {path}")

    df = pd.DataFrame(rows, columns=MC_COLUMNS)
    numeric_cols = [c for c in MC_COLUMNS if c != "type"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    df["p95_ms"] = df["p95"] / 1000.0  # mcperf latencies are microseconds
    return df


def read_raw_cpu(path: Path, core_ids: Iterable[int]) -> pd.DataFrame:
    """Return timestamped aggregate utilization over the pinned cores.

    /proc/stat cpu fields are cumulative jiffies:
      user nice system idle iowait irq softirq steal guest guest_nice

    Utilization between two samples is:
      100 * (delta_total - delta_idle - delta_iowait) / delta_total

    We compute this per pinned core and sum across cores. Therefore C=1 ranges
    0..100, C=2 0..200, C=3 0..300.
    """
    wanted = {f"cpu{c}" for c in core_ids}
    samples: list[dict] = []
    current_epoch: int | None = None

    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("# sample"):
                m = SAMPLE_RE.search(line)
                current_epoch = int(m.group("epoch_ms")) if m else None
                continue
            if current_epoch is None or not line.startswith("cpu"):
                continue
            parts = line.split()
            cpu_name = parts[0]
            if cpu_name not in wanted:
                continue
            vals = [int(x) for x in parts[1:]]
            # Pad to 10 fields to tolerate older kernels.
            vals += [0] * (10 - len(vals))
            user, nice, system, idle, iowait, irq, softirq, steal, guest, guest_nice = vals[:10]
            total = sum(vals[:10])
            idle_all = idle + iowait
            samples.append({
                "epoch_ms": current_epoch,
                "cpu": cpu_name,
                "total": total,
                "idle_all": idle_all,
            })

    if not samples:
        raise ValueError(f"No raw /proc/stat samples for cores {sorted(core_ids)} in {path}")

    raw = pd.DataFrame(samples).sort_values(["cpu", "epoch_ms"])
    raw["delta_total"] = raw.groupby("cpu")["total"].diff()
    raw["delta_idle"] = raw.groupby("cpu")["idle_all"].diff()
    raw = raw.dropna(subset=["delta_total", "delta_idle"])
    raw = raw[raw["delta_total"] > 0].copy()
    raw["core_util_pct"] = 100.0 * (raw["delta_total"] - raw["delta_idle"]) / raw["delta_total"]
    raw["core_util_pct"] = raw["core_util_pct"].clip(lower=0.0, upper=100.0)

    agg = (
        raw.groupby("epoch_ms", as_index=False)["core_util_pct"]
        .sum()
        .rename(columns={"core_util_pct": "cpu_util_pct"})
        .sort_values("epoch_ms")
    )
    return agg


def attach_cpu_to_mcperf(mc: pd.DataFrame, cpu: pd.DataFrame) -> pd.DataFrame:
    out = mc.copy()
    vals: list[float] = []
    for row in out.itertuples(index=False):
        in_window = cpu[(cpu["epoch_ms"] >= row.ts_start) & (cpu["epoch_ms"] <= row.ts_end)]
        if not in_window.empty:
            vals.append(float(in_window["cpu_util_pct"].mean()))
            continue
        # Fallback: nearest sample to interval midpoint. Useful when a mcperf step
        # is short and no 1-second CPU sample lands inside the exact interval.
        mid = (row.ts_start + row.ts_end) / 2.0
        idx = (cpu["epoch_ms"] - mid).abs().idxmin()
        vals.append(float(cpu.loc[idx, "cpu_util_pct"]))
    out["cpu_util_pct"] = vals
    return out


def load_all(result_dir: Path) -> pd.DataFrame:
    all_runs: list[pd.DataFrame] = []
    for mc_path in sorted(result_dir.glob("t*_c*_rep*.txt")):
        if mc_path.name.endswith("_cpu.txt"):
            continue
        run_label, c_count, core_ids, rep = parse_run_label(mc_path)
        cpu_path = result_dir / f"{run_label}_cpu.txt"
        mc = read_mcperf(mc_path)
        cpu = read_raw_cpu(cpu_path, core_ids)
        joined = attach_cpu_to_mcperf(mc, cpu)
        joined["run"] = run_label
        joined["C"] = c_count
        joined["rep"] = rep
        joined["cores"] = ",".join(map(str, core_ids))
        all_runs.append(joined)

    if not all_runs:
        raise ValueError(f"No run files found in {result_dir}")
    return pd.concat(all_runs, ignore_index=True)


def summarize(joined: pd.DataFrame) -> pd.DataFrame:
    return (
        joined.groupby(["C", "target"], as_index=False)
        .agg(
            QPS=("QPS", "mean"),
            p95_ms=("p95_ms", "mean"),
            cpu_util_pct=("cpu_util_pct", "mean"),
        )
        .sort_values(["C", "QPS"])
    )


def make_plots(summary: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for c_count in [1, 2, 3]:
        df = summary[summary["C"] == c_count].sort_values("QPS")
        if df.empty:
            print(f"Skipping C={c_count}: no data")
            continue

        fig, ax1 = plt.subplots(figsize=(8, 5))
        ax2 = ax1.twinx()

        ax1.plot(df["QPS"], df["p95_ms"], marker="o", label="p95 latency", color="blue")
        ax1.axhline(0.8, linestyle=":", linewidth=1.5, label="0.8 ms SLO", color="black")
        ax2.plot(df["QPS"], df["cpu_util_pct"], marker="s", label="CPU utilization", color="green")

        ax1.set_title(f"Memcached performance, C = {c_count}")
        ax1.set_xlabel("Achieved QPS")
        ax1.set_xlim(0, 125000)
        ax1.set_ylabel("95th percentile latency (ms)")
        ax2.set_ylabel("CPU utilization (%)")
        ax2.set_ylim(0, 100 * c_count)
        ax1.grid(True, alpha=0.3)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="best")

        fig.tight_layout()
        out = out_dir / f"memcached_C{c_count}.png"
        fig.savefig(out, dpi=200)
        plt.close(fig)
        print(f"Wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()

    result_dir = args.result_dir
    out_dir = result_dir / "plots"
    joined = load_all(result_dir)
    summary = summarize(joined)

    out_dir.mkdir(parents=True, exist_ok=True)
    joined.to_csv(out_dir / "memcached_joined_per_run.csv", index=False)
    summary.to_csv(out_dir / "memcached_summary.csv", index=False)
    make_plots(summary, out_dir)


if __name__ == "__main__":
    main()
