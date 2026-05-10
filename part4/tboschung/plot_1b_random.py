#!/usr/bin/env python3
"""
Visualize memcached RANDOM_QPS experiments using mcperf output plus raw /proc/stat CPU scrapes.

This script is intentionally for RANDOM_QPS runs only. It does not support the old --scan load.

Expected files in a result directory:
  t3_c0_rep1.txt
  t3_c0_rep1_cpu.txt
  t3_c0_1_rep2.txt
  t3_c0_1_rep2_cpu.txt
  t3_c0_1_2_rep3.txt
  t3_c0_1_2_rep3_cpu.txt

RANDOM_QPS generation example:
  mcperf ... -t 120 --qps_interval 10 --qps_min 5000 --qps_max 110000

The important difference from scan mode is that consecutive rows are not a monotonic sweep.
Rows are plotted in time order, and target-QPS intervals are treated as random samples.

Outputs under <result_dir>/plots_random_qps/:
  random_qps_joined_per_interval.csv
  random_qps_summary_by_target.csv
  random_qps_timeseries_<run>.png
  random_qps_target_vs_achieved_C*.png
  random_qps_latency_vs_qps_C*.png
  random_qps_cpu_vs_qps_C*.png
  random_qps_latency_cpu_qps_C*.png
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import pandas as pd

MC_BASE_COLUMNS = [
    "type", "avg", "std", "min", "p5", "p10", "p50", "p67", "p75", "p80",
    "p85", "p90", "p95", "p99", "p999", "p9999", "QPS", "target",
]
MC_COLUMNS = MC_BASE_COLUMNS + ["ts_start", "ts_end"]

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
    run_ts_start: int | None = None
    run_ts_end: int | None = None

    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("Timestamp start:"):
                run_ts_start = int(line.split(":", 1)[1].strip())
                continue
            if line.startswith("Timestamp end:"):
                run_ts_end = int(line.split(":", 1)[1].strip())
                continue
            if not line or line.startswith("#") or line.startswith("Warning!") or line.startswith("CPU Usage"):
                continue
            parts = line.split()
            if not parts or parts[0] != "read":
                continue
            if len(parts) >= len(MC_COLUMNS):
                # Newer output format: each read row already carries interval timestamps.
                rows.append(parts[: len(MC_COLUMNS)])
            elif len(parts) >= len(MC_BASE_COLUMNS):
                # Older/current mcperf output: rows end at target; synthesize interval
                # timestamps from the run-level Timestamp start/end header.
                rows.append(parts[: len(MC_BASE_COLUMNS)])

    if not rows:
        raise ValueError(
            f"No mcperf read rows found in {path}. Expected lines starting with 'read' "
            f"and at least {len(MC_BASE_COLUMNS)} whitespace-separated columns."
        )

    if len(rows[0]) == len(MC_COLUMNS):
        df = pd.DataFrame(rows, columns=MC_COLUMNS)
    else:
        if run_ts_start is None or run_ts_end is None:
            raise ValueError(
                f"{path} has mcperf rows without per-interval timestamps, and the "
                "Timestamp start/end header is missing."
            )
        df = pd.DataFrame(rows, columns=MC_BASE_COLUMNS)
        # Split the total run duration evenly across the random-QPS rows. This
        # matches mcperf's fixed qps_interval output and is accurate enough for
        # attaching one-second /proc/stat samples to each interval.
        step_ms = (run_ts_end - run_ts_start) / len(df)
        df["ts_start"] = [int(round(run_ts_start + i * step_ms)) for i in range(len(df))]
        df["ts_end"] = [int(round(run_ts_start + (i + 1) * step_ms)) for i in range(len(df))]
        df.loc[df.index[-1], "ts_end"] = run_ts_end

    numeric_cols = [c for c in MC_COLUMNS if c != "type"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    df["p95_ms"] = df["p95"] / 1000.0
    df["p99_ms"] = df["p99"] / 1000.0
    df["avg_ms"] = df["avg"] / 1000.0
    df["interval_s"] = (df["ts_end"] - df["ts_start"]) / 1000.0
    df["elapsed_s"] = (df["ts_start"] - df["ts_start"].min()) / 1000.0
    df["interval_idx"] = range(len(df))
    df["qps_error"] = df["QPS"] - df["target"]
    df["qps_error_pct"] = 100.0 * df["qps_error"] / df["target"].replace(0, pd.NA)
    return df


def read_raw_cpu(path: Path, core_ids: Iterable[int]) -> pd.DataFrame:
    """Return timestamped aggregate utilization over pinned cores.

    Utilization is computed between adjacent /proc/stat samples per pinned core,
    then summed across pinned cores. Therefore C=1 ranges 0..100, C=2 0..200,
    and C=3 0..300.
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
            idle_all = vals[3] + vals[4]
            total = sum(vals[:10])
            samples.append({"epoch_ms": current_epoch, "cpu": cpu_name, "total": total, "idle_all": idle_all})

    if not samples:
        raise ValueError(f"No raw /proc/stat samples for cores {sorted(core_ids)} in {path}")

    raw = pd.DataFrame(samples).sort_values(["cpu", "epoch_ms"])
    raw["delta_total"] = raw.groupby("cpu")["total"].diff()
    raw["delta_idle"] = raw.groupby("cpu")["idle_all"].diff()
    raw = raw.dropna(subset=["delta_total", "delta_idle"])
    raw = raw[raw["delta_total"] > 0].copy()
    raw["core_util_pct"] = 100.0 * (raw["delta_total"] - raw["delta_idle"]) / raw["delta_total"]
    raw["core_util_pct"] = raw["core_util_pct"].clip(lower=0.0, upper=100.0)

    return (
        raw.groupby("epoch_ms", as_index=False)["core_util_pct"]
        .sum()
        .rename(columns={"core_util_pct": "cpu_util_pct"})
        .sort_values("epoch_ms")
    )


def attach_cpu_to_mcperf(mc: pd.DataFrame, cpu: pd.DataFrame) -> pd.DataFrame:
    out = mc.copy()
    means: list[float] = []
    p95s: list[float] = []
    maxes: list[float] = []
    sample_counts: list[int] = []

    for row in out.itertuples(index=False):
        # CPU samples are one-second-ish points; each random QPS interval is expected to be ~10 s.
        in_window = cpu[(cpu["epoch_ms"] >= row.ts_start) & (cpu["epoch_ms"] <= row.ts_end)]
        if in_window.empty:
            mid = (row.ts_start + row.ts_end) / 2.0
            idx = (cpu["epoch_ms"] - mid).abs().idxmin()
            vals = pd.Series([float(cpu.loc[idx, "cpu_util_pct"])])
        else:
            vals = in_window["cpu_util_pct"].astype(float)

        means.append(float(vals.mean()))
        p95s.append(float(vals.quantile(0.95)))
        maxes.append(float(vals.max()))
        sample_counts.append(int(vals.size))

    out["cpu_util_pct"] = means
    out["cpu_util_p95_pct"] = p95s
    out["cpu_util_max_pct"] = maxes
    out["cpu_samples"] = sample_counts
    return out


def assert_random_qps_only(joined: pd.DataFrame) -> None:
    # Old scan mode in the provided generator used ~5 second steps; random mode uses 10 second intervals.
    median_interval = float(joined["interval_s"].median())
    if median_interval < 7.0:
        raise ValueError(
            f"This script only supports RANDOM_QPS runs. Median interval is {median_interval:.2f}s, "
            "which looks like the old short scan load."
        )

    # Random target order should usually not be monotonically increasing. This is a guardrail, not a parser.
    monotonic_runs = []
    for run, df in joined.groupby("run"):
        if df["target"].is_monotonic_increasing and df["target"].nunique() > 3:
            monotonic_runs.append(run)
    if monotonic_runs:
        raise ValueError(
            "This script only supports RANDOM_QPS runs. These runs look like monotonic sweeps: "
            + ", ".join(monotonic_runs)
        )


def load_all(result_dir: Path) -> pd.DataFrame:
    all_runs: list[pd.DataFrame] = []
    for mc_path in sorted(result_dir.glob("t*_c*_rep*.txt")):
        if mc_path.name.endswith("_cpu.txt"):
            continue
        run_label, c_count, core_ids, rep = parse_run_label(mc_path)
        cpu_path = result_dir / f"{run_label}_cpu.txt"
        if not cpu_path.exists():
            raise FileNotFoundError(f"Missing CPU file: {cpu_path}")

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

    joined = pd.concat(all_runs, ignore_index=True)
    assert_random_qps_only(joined)
    return joined.sort_values(["C", "run", "interval_idx"]).reset_index(drop=True)


def summarize_by_target(joined: pd.DataFrame) -> pd.DataFrame:
    return (
        joined.groupby(["C", "target"], as_index=False)
        .agg(
            intervals=("target", "size"),
            achieved_qps_mean=("QPS", "mean"),
            achieved_qps_std=("QPS", "std"),
            qps_error_pct_mean=("qps_error_pct", "mean"),
            p95_ms_mean=("p95_ms", "mean"),
            p95_ms_p95=("p95_ms", lambda s: float(s.quantile(0.95))),
            p99_ms_mean=("p99_ms", "mean"),
            cpu_util_pct_mean=("cpu_util_pct", "mean"),
            cpu_util_p95_pct=("cpu_util_pct", lambda s: float(s.quantile(0.95))),
        )
        .sort_values(["C", "target"])
    )


def savefig(fig: plt.Figure, out: Path) -> None:
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)
    print(f"Wrote {out}")


def annotate_slo(ax: plt.Axes, slo_ms: float) -> None:
    ax.axhline(slo_ms, linestyle=":", linewidth=1.5, label=f"{slo_ms:g} ms SLO")


def plot_timeseries(df: pd.DataFrame, out_dir: Path, slo_ms: float) -> None:
    for run, run_df in df.groupby("run"):
        run_df = run_df.sort_values("elapsed_s")
        c_count = int(run_df["C"].iloc[0])

        fig, axes = plt.subplots(4, 1, figsize=(11, 10), sharex=True)
        x = run_df["elapsed_s"]
        axes[0].step(x, run_df["target"], where="post", linewidth=1.8, label="target")
        axes[0].plot(x, run_df["QPS"], marker="o", linewidth=1.0, markersize=3, label="achieved")
        axes[1].plot(x, run_df["p95_ms"], marker="o", linewidth=1.0, markersize=3, label="p95 latency")
        axes[2].plot(x, run_df["cpu_util_pct"], marker="o", linewidth=1.0, markersize=3, label="CPU util")
        axes[3].plot(x, run_df["qps_error_pct"], marker="o", linewidth=1.0, markersize=3, label="QPS error")

        axes[0].set_ylabel("QPS")
        axes[0].set_title(f"Random-QPS load over time, run = {run}, C = {c_count}")
        axes[1].set_ylabel("p95 latency (ms)")
        annotate_slo(axes[1], slo_ms)
        axes[2].set_ylabel("CPU util (%)")
        axes[2].set_ylim(0, 100 * c_count)
        axes[3].set_ylabel("QPS error (%)")
        axes[3].set_xlabel("Elapsed time (s)")
        axes[3].axhline(0, linestyle=":", linewidth=1.0)

        for ax in axes:
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best", fontsize="small")

        savefig(fig, out_dir / f"random_qps_timeseries_{run}.png")


def plot_target_vs_achieved(df: pd.DataFrame, out_dir: Path) -> None:
    c_count = int(df["C"].iloc[0])
    fig, ax = plt.subplots(figsize=(7, 7))
    for run, run_df in df.groupby("run"):
        ax.scatter(run_df["target"], run_df["QPS"], s=30, alpha=0.75, label=run)

    lo = min(float(df["target"].min()), float(df["QPS"].min()))
    hi = max(float(df["target"].max()), float(df["QPS"].max()))
    ax.plot([lo, hi], [lo, hi], linestyle=":", linewidth=1.5, label="ideal achieved = target")
    ax.set_title(f"Target vs achieved QPS, C = {c_count}")
    ax.set_xlabel("Target QPS")
    ax.set_ylabel("Achieved QPS")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize="small")
    savefig(fig, out_dir / f"random_qps_target_vs_achieved_C{c_count}.png")


def plot_scatter_metrics(df: pd.DataFrame, out_dir: Path, slo_ms: float) -> None:
    c_count = int(df["C"].iloc[0])

    fig, ax = plt.subplots(figsize=(8, 5))
    scatter = ax.scatter(df["QPS"], df["p95_ms"], c=df["target"], s=45, alpha=0.8)
    annotate_slo(ax, slo_ms)
    ax.set_title(f"Random-QPS latency samples, C = {c_count}")
    ax.set_xlabel("Achieved QPS")
    ax.set_ylabel("p95 latency (ms)")
    ax.grid(True, alpha=0.3)
    fig.colorbar(scatter, ax=ax, label="Target QPS")
    ax.legend(loc="best")
    savefig(fig, out_dir / f"random_qps_latency_vs_qps_C{c_count}.png")

    fig, ax = plt.subplots(figsize=(8, 5))
    scatter = ax.scatter(df["QPS"], df["cpu_util_pct"], c=df["target"], s=45, alpha=0.8)
    ax.set_title(f"Random-QPS CPU samples, C = {c_count}")
    ax.set_xlabel("Achieved QPS")
    ax.set_ylabel("CPU utilization (%)")
    ax.set_ylim(0, 100 * c_count)
    ax.grid(True, alpha=0.3)
    fig.colorbar(scatter, ax=ax, label="Target QPS")
    savefig(fig, out_dir / f"random_qps_cpu_vs_qps_C{c_count}.png")

    fig, ax = plt.subplots(figsize=(8, 5))
    scatter = ax.scatter(df["cpu_util_pct"], df["p95_ms"], c=df["QPS"], s=45, alpha=0.8)
    annotate_slo(ax, slo_ms)
    ax.set_title(f"Latency vs CPU under random QPS, C = {c_count}")
    ax.set_xlabel("CPU utilization (%)")
    ax.set_ylabel("p95 latency (ms)")
    ax.set_xlim(0, 100 * c_count)
    ax.grid(True, alpha=0.3)
    fig.colorbar(scatter, ax=ax, label="Achieved QPS")
    ax.legend(loc="best")
    savefig(fig, out_dir / f"random_qps_latency_cpu_qps_C{c_count}.png")


def print_summary(joined: pd.DataFrame, slo_ms: float) -> None:
    print("\nRandom-QPS interval summary")
    print("===========================")
    for c_count, df in joined.groupby("C"):
        bad = int((df["p95_ms"] > slo_ms).sum())
        total = int(len(df))
        qps_abs_err = df["qps_error_pct"].abs()
        print(
            f"C={c_count}: intervals={total}, "
            f"target range={df['target'].min():.0f}..{df['target'].max():.0f}, "
            f"achieved range={df['QPS'].min():.0f}..{df['QPS'].max():.0f}, "
            f"median |QPS error|={qps_abs_err.median():.2f}%, "
            f"p95 latency range={df['p95_ms'].min():.3f}..{df['p95_ms'].max():.3f} ms, "
            f"SLO violations={bad}/{total}"
        )


def make_plots(joined: pd.DataFrame, out_dir: Path, slo_ms: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for c_count, df in joined.groupby("C"):
        df = df.sort_values(["run", "interval_idx"])
        plot_timeseries(df, out_dir, slo_ms)
        plot_target_vs_achieved(df, out_dir)
        plot_scatter_metrics(df, out_dir, slo_ms)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot RANDOM_QPS memcached load results only.")
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--slo-ms", type=float, default=0.8, help="p95 latency SLO line in milliseconds")
    args = parser.parse_args()

    result_dir = args.result_dir
    out_dir = result_dir / "plots_random_qps"

    joined = load_all(result_dir)
    summary = summarize_by_target(joined)

    out_dir.mkdir(parents=True, exist_ok=True)
    joined.to_csv(out_dir / "random_qps_joined_per_interval.csv", index=False)
    summary.to_csv(out_dir / "random_qps_summary_by_target.csv", index=False)
    make_plots(joined, out_dir, args.slo_ms)
    print_summary(joined, args.slo_ms)


if __name__ == "__main__":
    main()
