#!/usr/bin/env python3
"""
Plot p95 latency vs achieved QPS for each memcached configuration,
averaged over repetitions.

9 configs: {1,2,3} threads x {1,2,3} cores
  - Color encodes thread count  (3 distinct colors)
  - Linestyle encodes core count (3 distinct styles)
  - Direct end-of-line labels replace the legend


Usage:
    python3 plot_results.py --results_dir results/20260426_123456
    python3 plot_results.py --results_dir results/20260426_123456 --out latency.png
"""

import os
import re
import argparse
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.patheffects as pe
import numpy as np

# ── Column indices in the data rows ──────────────────────────────────────────
# #type avg std min p5 p10 p50 p67 p75 p80 p85 p90 p95 p99 p999 p9999 QPS target ...
COL_P95 = 12
COL_QPS = 16

# ── Visual encoding ───────────────────────────────────────────────────────────
# Color = thread count
THREAD_COLORS = {
    1: "#2166ac",   # blue
    2: "#d6604d",   # red-orange
    3: "#1a9641",   # green
}

# Linestyle = core count
CORE_STYLES = {
    1: (0, ()),           # solid
    2: (0, (6, 2)),       # dashed
    3: (0, (2, 2)),       # dotted
}

MARKERS = {
    1: "o",
    2: "s",
    3: "^",
}


def parse_result_file(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("Warning") or line.startswith("CPU"):
                continue
            parts = line.split()
            if parts[0] != "read":
                continue
            try:
                rows.append({
                    "qps": float(parts[COL_QPS]),
                    "p95": float(parts[COL_P95]),
                })
            except (IndexError, ValueError):
                continue
    return rows


def collect_runs(results_dir: str) -> dict[str, list[list[dict]]]:
    runs = defaultdict(list)
    for fname in sorted(os.listdir(results_dir)):
        if not fname.endswith(".txt"):
            continue
        match = re.match(r"^(.+)_rep\d+\.txt$", fname)
        if not match:
            print(f"  [skip] unrecognised filename: {fname}")
            continue
        config = match.group(1)
        rows = parse_result_file(os.path.join(results_dir, fname))
        if rows:
            runs[config].append(rows)
        else:
            print(f"  [warn] no data rows in {fname}")
    return dict(runs)


def average_runs(run_list: list[list[dict]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    min_len = min(len(r) for r in run_list)

    qps_arr = np.array([[r["qps"] for r in rep[:min_len]] for rep in run_list])
    p95_arr = np.array([[r["p95"] for r in rep[:min_len]] for rep in run_list])

    qps_mean = np.mean(qps_arr, axis=0)
    p95_mean = np.mean(p95_arr, axis=0)

    # variability across repetitions
    p95_std = np.std(p95_arr, axis=0)

    return qps_mean, p95_mean, p95_std


def extract_thread_cores(config: str) -> tuple[int, int]:
    """
    Parse thread and core count from config label.
    Expects format like: t2_c0_1  (2 threads, 2 cores: 0 and 1)
    Returns (n_threads, n_cores).
    """
    m = re.match(r"t(\d+)_c(.+)", config)
    if not m:
        return 1, 1
    n_threads = int(m.group(1))
    n_cores   = len(m.group(2).split("_"))
    return n_threads, n_cores


def plot(runs: dict[str, list[list[dict]]], out_path: str):
    fig, ax = plt.subplots(figsize=(13, 7))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f8f8f8")

    lines_meta = []  # (qps, p95, config, n_threads, n_cores) for label placement

    for config, rep_list in sorted(runs.items()):
        qps, p95, p95_std = average_runs(rep_list)
        order     = np.argsort(qps)
        qps, p95, p95_std = qps[order], p95[order], p95_std[order]

        n_threads, n_cores = extract_thread_cores(config)
        color  = THREAD_COLORS.get(n_threads, "#555555")
        lstyle = CORE_STYLES.get(n_cores,   (0, ()))
        marker = MARKERS.get(n_cores, "o")

        ax.errorbar(
            qps, p95,
            yerr=p95_std,
            color=color,
            linestyle=lstyle,
            marker=marker,
            markersize=5,
            linewidth=2.2,
            elinewidth=1.2,
            capsize=3,
            alpha=0.9,
            markevery=2,
            zorder=3,
        )
        lines_meta.append((qps, p95, config, n_threads, n_cores, color))

    # ── Direct end-of-line labels ─────────────────────────────────────────────
    for qps, p95, config, n_threads, n_cores, color in lines_meta:
        x_end, y_end = qps[-1], p95[-1]
        label = f"t{n_threads} · {n_cores}c"
        ax.annotate(
            label,
            xy=(x_end, y_end),
            xytext=(8, 0),
            textcoords="offset points",
            fontsize=9,
            fontweight="bold",
            color=color,
            va="center",
            path_effects=[pe.withStroke(linewidth=3, foreground="white")],
        )

    # ── Encoding key (compact, two-part) ─────────────────────────────────────
    # Thread color patches
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    color_handles = [
        Patch(facecolor=THREAD_COLORS[t], label=f"{t} thread{'s' if t>1 else ''}")
        for t in sorted(THREAD_COLORS)
    ]
    style_handles = [
        Line2D([0], [0], color="#555", linestyle=CORE_STYLES[c], marker=MARKERS[c],
               markersize=6, linewidth=2, label=f"{c} core{'s' if c>1 else ''}")
        for c in sorted(CORE_STYLES)
    ]

    leg1 = ax.legend(handles=color_handles, title="Color = threads",
                     loc="upper left", fontsize=9, title_fontsize=9,
                     framealpha=0.9, edgecolor="#cccccc")
    ax.add_artist(leg1)
    ax.legend(handles=style_handles, title="Style = cores",
              loc="upper left", bbox_to_anchor=(0, 0.72),
              fontsize=9, title_fontsize=9,
              framealpha=0.9, edgecolor="#cccccc")

    # ── Axes ─────────────────────────────────────────────────────────────────
    ax.set_xlabel("Achieved QPS", fontsize=13, labelpad=8)
    ax.set_ylabel("p95 Latency", fontsize=13, labelpad=8)
    ax.set_title("Memcached: p95 Latency vs Achieved QPS\n(averaged over 3 repetitions per config)",
                 fontsize=14, pad=14)

    ax.xaxis.set_major_formatter(ticker.FuncFormatter(
        lambda x, _: f"{int(x/1000)}k" if x >= 1000 else str(int(x))
    ))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(
        lambda y, _: f"{y/1000:.1f} ms" if y >= 1000 else f"{int(y)} µs"
    ))

    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6, color="#cccccc")
    ax.tick_params(labelsize=10)

    # Extra right margin so end-of-line labels don't get clipped
    xmax = max(qps[-1] for qps, *_ in lines_meta)
    ax.set_xlim(right=xmax * 1.18)

    fig.tight_layout()
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    print(f"Plot saved → {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Plot memcached benchmark results")
    parser.add_argument("--results_dir", required=True,
                        help="Path to results directory (e.g. results/20260426_123456)")
    parser.add_argument("--out", default="latency_vs_qps.png",
                        help="Output image path (default: latency_vs_qps.png)")
    args = parser.parse_args()

    if not os.path.isdir(args.results_dir):
        print(f"Error: results_dir not found: {args.results_dir}")
        return

    print(f"Scanning {args.results_dir} ...")
    runs = collect_runs(args.results_dir)

    if not runs:
        print("No valid result files found.")
        return

    print(f"Found {len(runs)} configuration(s):")
    for cfg, reps in sorted(runs.items()):
        print(f"  {cfg}: {len(reps)} rep(s), {min(len(r) for r in reps)} scan steps each")

    plot(runs, args.out)


if __name__ == "__main__":
    main()