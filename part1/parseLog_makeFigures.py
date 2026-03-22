#!/usr/bin/env python3
import re
import numpy as np
import matplotlib.pyplot as plt

# ── parser (provided) ────────────────────────────────────────────────────────

def parse_memcache_log(text):
    header_match = re.search(r'^#type\s+(.+)$', text, re.MULTILINE)
    if not header_match:
        raise ValueError("No header found")
    columns = ['type'] + header_match.group(1).split()

    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('Warning') or line.startswith('CPU'):
            continue
        parts = line.split()
        if len(parts) == len(columns):
            row = {col: (parts[i] if i == 0 else float(parts[i]))
                   for i, col in enumerate(columns)}
            rows.append(row)

    return columns, rows

# ── config ────────────────────────────────────────────────────────────────────

CONFIGS = ['baseline', 'cpu', 'l1d', 'l1i', 'l2', 'llc', 'membw']
RUNS    = [1, 2, 3]
LOG_DIR = 'logs'
EPSILON = 0.05  # fractional padding on each axis (5%)

# ── load & average runs ───────────────────────────────────────────────────────

def load_config(config):
    """Return per-target-point arrays: mean_qps, std_qps, mean_p95, std_p95."""
    all_runs = []
    for run in RUNS:
        path = f"{LOG_DIR}/part1_{config}_mcperf_run{run}.txt"
        with open(path) as f:
            _, rows = parse_memcache_log(f.read())
        all_runs.append([(r['QPS'], r['p95']) for r in rows])

    n_points = min(len(r) for r in all_runs)
    qps_mat  = np.array([[run[i][0] for run in all_runs] for i in range(n_points)])
    p95_mat  = np.array([[run[i][1] for run in all_runs] for i in range(n_points)])

    return (qps_mat.mean(1), qps_mat.std(1),
            p95_mat.mean(1), p95_mat.std(1))

# ── helper: compute axis limits with epsilon padding ─────────────────────────

def axis_limits(values, errors=None, zero_origin=True):
    """Return (lo, hi) with EPSILON fractional padding beyond the data range.
    If zero_origin=True the lower bound is always 0."""
    hi_vals = values + errors if errors is not None else values
    lo_vals = values - errors if errors is not None else values
    lo = 0 if zero_origin else lo_vals.min()
    hi = hi_vals.max()
    span = hi - lo if hi != lo else abs(hi) or 1.0
    return lo, hi + EPSILON * span

# ── plot all configs on one combined figure ───────────────────────────────────

def plot_combined(data):
    fig, ax = plt.subplots(figsize=(9, 5))

    all_qps = np.concatenate([d[0] + d[1] for d in data.values()])
    all_p95 = np.concatenate([(d[2] + d[3]) / 1000 for d in data.values()])
    _, x_hi = axis_limits(all_qps, zero_origin=True)
    _, y_hi = axis_limits(all_p95, zero_origin=True)

    for config, (mean_qps, std_qps, mean_p95, std_p95) in data.items():
        ax.errorbar(mean_qps, mean_p95 / 1000,
                    xerr=std_qps, yerr=std_p95 / 1000,
                    label=config, marker='o', markersize=4,
                    capsize=3, linewidth=1.5)

    ax.set_xlim(0, x_hi)
    ax.set_ylim(0, y_hi)
    ax.set_xlabel('Achieved QPS')
    ax.set_ylabel('95th Percentile Latency (ms)')
    ax.set_title(f'Memcached Latency vs Throughput (averaged over {len(RUNS)} runs)')
    ax.legend(title='Configuration')
    ax.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('mcperf_plot.png', dpi=150)
    print("Saved mcperf_plot.png")
    plt.close(fig)

# ── plot one figure per config ────────────────────────────────────────────────

def plot_per_config(data):
    for config, (mean_qps, std_qps, mean_p95, std_p95) in data.items():
        p95_ms     = mean_p95 / 1000
        std_p95_ms = std_p95  / 1000

        _, x_hi = axis_limits(mean_qps, errors=std_qps,  zero_origin=True)
        _, y_hi = axis_limits(p95_ms,   errors=std_p95_ms, zero_origin=True)

        fig, ax = plt.subplots(figsize=(7, 4))
        ax.errorbar(mean_qps, p95_ms,
                    xerr=std_qps, yerr=std_p95_ms,
                    marker='o', markersize=5,
                    capsize=4, linewidth=1.8, color='steelblue')

        ax.set_xlim(0, 40_000)
        ax.set_ylim(0, 20)
        ax.set_xlabel('Achieved QPS')
        ax.set_ylabel('95th Percentile Latency (ms)')
        ax.set_title(f'Config: {config}  (averaged over {len(RUNS)} runs)')
        ax.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()

        fname = f'mcperf_plot_{config}.png'
        plt.savefig(fname, dpi=150)
        print(f"Saved {fname}")
        plt.close(fig)

# ── main ──────────────────────────────────────────────────────────────────────

data = {config: load_config(config) for config in CONFIGS}
plot_combined(data)
plot_per_config(data)