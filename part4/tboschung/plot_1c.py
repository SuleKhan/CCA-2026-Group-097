#!/usr/bin/env python3
"""
Create memcached performance plots using raw /proc/stat CPU scrape files.

Expected files in a result directory:

  t3_c0_int_none_rep1.txt
  t3_c0_int_none_rep1_cpu.txt

  t3_c0_1_2_int_none_rep1.txt
  t3_c0_1_2_int_none_rep1_cpu.txt

  t3_c0_1_2_int_memory_rep1.txt
  t3_c0_1_2_int_memory_rep1_cpu.txt

CPU file format:

  # sample epoch_ms=1777828232000 iso_ts=... host=...
  cpu0  123 0 456 789 ...
  cpu1  ...
  cpu2  ...
  cpu3  ...

Usage:

  python3 plot_memcached_interference.py results_interference/20260509_000000
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

# New run labels:
#   t3_c0_int_none_rep1
#   t3_c0_1_2_int_memory_rep3
RUN_RE = re.compile(
    r"^t(?P<threads>\d+)"
    r"_c(?P<cores>[0-9_]+)"
    r"_int_(?P<interference>[A-Za-z0-9_-]+)"
    r"_rep(?P<rep>\d+)$"
)

SAMPLE_RE = re.compile(r"epoch_ms=(?P<epoch_ms>\d+)")


def parse_run_label(path: Path) -> dict:
    label = path.stem

    m = RUN_RE.match(label)
    if not m:
        raise ValueError(
            f"Cannot parse run label from {path.name}; expected something like "
            f"t3_c0_1_2_int_memory_rep1.txt"
        )

    core_ids = [int(x) for x in m.group("cores").split("_")]

    return {
        "run": label,
        "threads": int(m.group("threads")),
        "C": len(core_ids),
        "core_ids": core_ids,
        "cores": ",".join(map(str, core_ids)),
        "interference": m.group("interference"),
        "rep": int(m.group("rep")),
    }


def read_mcperf(path: Path) -> pd.DataFrame:
    rows: list[list[str]] = []

    with path.open() as f:
        for raw in f:
            line = raw.strip()

            if (
                not line
                or line.startswith("#")
                or line.startswith("Warning!")
                or line.startswith("CPU Usage")
            ):
                continue

            parts = line.split()

            if parts and parts[0] == "read" and len(parts) >= len(MC_COLUMNS):
                rows.append(parts[: len(MC_COLUMNS)])

    if not rows:
        raise ValueError(f"No mcperf read rows found in {path}")

    df = pd.DataFrame(rows, columns=MC_COLUMNS)

    numeric_cols = [c for c in MC_COLUMNS if c != "type"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")

    # mcperf reports latency in microseconds.
    df["p95_ms"] = df["p95"] / 1000.0
    df["p99_ms"] = df["p99"] / 1000.0
    df["avg_ms"] = df["avg"] / 1000.0

    return df


def read_raw_cpu(path: Path, core_ids: Iterable[int]) -> pd.DataFrame:
    """
    Return timestamped aggregate utilization over the selected cores.

    /proc/stat cpu fields are cumulative jiffies:

      user nice system idle iowait irq softirq steal guest guest_nice

    Per-core utilization between two samples is:

      100 * (delta_total - delta_idle - delta_iowait) / delta_total

    We compute this per selected core and sum across cores.

    Therefore:
      C=1 ranges 0..100
      C=2 ranges 0..200
      C=3 ranges 0..300
      C=4 ranges 0..400
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
            vals += [0] * (10 - len(vals))

            user, nice, system, idle, iowait, irq, softirq, steal, guest, guest_nice = vals[:10]

            total = sum(vals[:10])
            idle_all = idle + iowait

            samples.append(
                {
                    "epoch_ms": current_epoch,
                    "cpu": cpu_name,
                    "total": total,
                    "idle_all": idle_all,
                }
            )

    if not samples:
        raise ValueError(f"No raw /proc/stat samples for cores {sorted(core_ids)} in {path}")

    raw = pd.DataFrame(samples).sort_values(["cpu", "epoch_ms"])

    raw["delta_total"] = raw.groupby("cpu")["total"].diff()
    raw["delta_idle"] = raw.groupby("cpu")["idle_all"].diff()

    raw = raw.dropna(subset=["delta_total", "delta_idle"])
    raw = raw[raw["delta_total"] > 0].copy()

    raw["core_util_pct"] = (
        100.0 * (raw["delta_total"] - raw["delta_idle"]) / raw["delta_total"]
    )

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
        in_window = cpu[
            (cpu["epoch_ms"] >= row.ts_start)
            & (cpu["epoch_ms"] <= row.ts_end)
        ]

        if not in_window.empty:
            vals.append(float(in_window["cpu_util_pct"].mean()))
            continue

        # Fallback: nearest sample to interval midpoint.
        mid = (row.ts_start + row.ts_end) / 2.0
        idx = (cpu["epoch_ms"] - mid).abs().idxmin()
        vals.append(float(cpu.loc[idx, "cpu_util_pct"]))

    out["cpu_util_pct"] = vals
    return out


def load_all(result_dir: Path) -> pd.DataFrame:
    all_runs: list[pd.DataFrame] = []

    for mc_path in sorted(result_dir.glob("t*_c*_int_*_rep*.txt")):
        if mc_path.name.endswith("_cpu.txt"):
            continue

        meta = parse_run_label(mc_path)

        cpu_path = result_dir / f"{meta['run']}_cpu.txt"
        if not cpu_path.exists():
            print(f"Skipping {mc_path.name}: missing CPU file {cpu_path.name}")
            continue

        mc = read_mcperf(mc_path)
        cpu = read_raw_cpu(cpu_path, meta["core_ids"])
        joined = attach_cpu_to_mcperf(mc, cpu)

        joined["run"] = meta["run"]
        joined["threads"] = meta["threads"]
        joined["C"] = meta["C"]
        joined["cores"] = meta["cores"]
        joined["interference"] = meta["interference"]
        joined["rep"] = meta["rep"]

        all_runs.append(joined)

    if not all_runs:
        raise ValueError(f"No new-format run files found in {result_dir}")

    return pd.concat(all_runs, ignore_index=True)


def summarize(joined: pd.DataFrame) -> pd.DataFrame:
    """
    Average across repetitions for each:
      C, cores, interference, target QPS
    """
    return (
        joined.groupby(["threads", "C", "cores", "interference", "target"], as_index=False)
        .agg(
            QPS=("QPS", "mean"),
            p95_ms=("p95_ms", "mean"),
            p99_ms=("p99_ms", "mean"),
            avg_ms=("avg_ms", "mean"),
            cpu_util_pct=("cpu_util_pct", "mean"),
            runs=("run", "nunique"),
        )
        .sort_values(["C", "interference", "QPS"])
    )


def make_latency_plots(summary: pd.DataFrame, out_dir: Path, slo_ms: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    for c_count in sorted(summary["C"].unique()):
        df_c = summary[summary["C"] == c_count].copy()

        if df_c.empty:
            continue

        fig, ax = plt.subplots(figsize=(8, 5))

        for interference in sorted(df_c["interference"].unique()):
            df = df_c[df_c["interference"] == interference].sort_values("QPS")

            ax.plot(
                df["QPS"],
                df["p95_ms"],
                marker="o",
                label=f"p95, interference={interference}",
            )

        ax.axhline(
            slo_ms,
            linestyle=":",
            linewidth=1.5,
            label=f"{slo_ms:.3f} ms SLO",
        )

        cores = df_c["cores"].iloc[0]

        ax.set_title(f"Memcached p95 latency, C={c_count}, cores=[{cores}]")
        ax.set_xlabel("Achieved QPS")
        ax.set_ylabel("95th percentile latency (ms)")
        ax.set_xlim(0, 125000)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best")

        fig.tight_layout()

        out = out_dir / f"latency_C{c_count}.png"
        fig.savefig(out, dpi=200)
        plt.close(fig)

        print(f"Wrote {out}")


def make_cpu_plots(summary: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    for c_count in sorted(summary["C"].unique()):
        df_c = summary[summary["C"] == c_count].copy()

        if df_c.empty:
            continue

        fig, ax = plt.subplots(figsize=(8, 5))

        for interference in sorted(df_c["interference"].unique()):
            df = df_c[df_c["interference"] == interference].sort_values("QPS")

            ax.plot(
                df["QPS"],
                df["cpu_util_pct"],
                marker="s",
                label=f"CPU, interference={interference}",
            )

        cores = df_c["cores"].iloc[0]

        ax.set_title(f"Memcached pinned-core CPU use, C={c_count}, cores=[{cores}]")
        ax.set_xlabel("Achieved QPS")
        ax.set_ylabel("CPU utilization across pinned cores (%)")
        ax.set_xlim(0, 125000)
        ax.set_ylim(0, 100 * c_count)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best")

        fig.tight_layout()

        out = out_dir / f"cpu_C{c_count}.png"
        fig.savefig(out, dpi=200)
        plt.close(fig)

        print(f"Wrote {out}")


def make_combined_plots(summary: pd.DataFrame, out_dir: Path, slo_ms: float) -> None:
    """
    Combined latency + CPU plots, one per core count and interference mode.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    for (c_count, interference), df in summary.groupby(["C", "interference"]):
        df = df.sort_values("QPS")

        if df.empty:
            continue

        fig, ax1 = plt.subplots(figsize=(8, 5))
        ax2 = ax1.twinx()

        ax1.plot(
            df["QPS"],
            df["p95_ms"],
            marker="o",
            label="p95 latency",
        )

        ax1.axhline(
            slo_ms,
            linestyle=":",
            linewidth=1.5,
            label=f"{slo_ms:.3f} ms SLO",
        )

        ax2.plot(
            df["QPS"],
            df["cpu_util_pct"],
            marker="s",
            label="CPU utilization",
        )

        cores = df["cores"].iloc[0]

        ax1.set_title(
            f"Memcached performance, C={c_count}, cores=[{cores}], interference={interference}"
        )
        ax1.set_xlabel("Achieved QPS")
        ax1.set_ylabel("95th percentile latency (ms)")
        ax2.set_ylabel("CPU utilization across pinned cores (%)")

        ax1.set_xlim(0, 125000)
        ax2.set_ylim(0, 100 * c_count)

        ax1.grid(True, alpha=0.3)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="best")

        fig.tight_layout()

        out = out_dir / f"combined_C{c_count}_{interference}.png"
        fig.savefig(out, dpi=200)
        plt.close(fig)

        print(f"Wrote {out}")


def print_slo_summary(summary: pd.DataFrame, slo_ms: float) -> None:
    print()
    print("SLO summary")
    print("=" * 80)

    tmp = summary.copy()
    tmp["meets_slo"] = tmp["p95_ms"] <= slo_ms

    for (c_count, interference), df in tmp.groupby(["C", "interference"]):
        df = df.sort_values("QPS")
        passing = df[df["meets_slo"]]

        if passing.empty:
            max_qps = 0.0
        else:
            max_qps = float(passing["QPS"].max())

        print(
            f"C={c_count}, interference={interference:>8}: "
            f"max achieved QPS under SLO ≈ {max_qps:,.0f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument(
        "--slo-ms",
        type=float,
        default=0.8,
        help="SLO line in milliseconds. Default: 0.8 ms.",
    )

    args = parser.parse_args()

    result_dir = args.result_dir
    out_dir = result_dir / "plots"

    joined = load_all(result_dir)
    summary = summarize(joined)

    out_dir.mkdir(parents=True, exist_ok=True)

    joined.to_csv(out_dir / "memcached_joined_per_run.csv", index=False)
    summary.to_csv(out_dir / "memcached_summary.csv", index=False)

    make_latency_plots(summary, out_dir, args.slo_ms)
    make_cpu_plots(summary, out_dir)
    make_combined_plots(summary, out_dir, args.slo_ms)
    print_slo_summary(summary, args.slo_ms)


if __name__ == "__main__":
    main()