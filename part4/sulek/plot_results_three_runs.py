#!/usr/bin/env python3
"""
Generate the Task 4.4 evaluation plots and metrics for three scheduler runs.

For each run this script creates one figure with three shared-x subplots:
  1. per-core workload allocation timeline, with paused intervals hatched
  2. memcached QPS and p95 latency on two y-axes
  3. per-core CPU utilisation

It also writes CSV metric summaries:
  - per_run_metrics.csv
  - per_job_runtime_summary.csv
  - makespan_summary.csv

Default input layout:
  final_scheduler/Task 4.4/QPS_5/Run 1/{measure_log.txt,jobs_log.txt,cpu_usage.log}
  final_scheduler/Task 4.4/QPS_5/Run 2/{...}
  final_scheduler/Task 4.4/QPS_5/Run 3/{...}

Usage:
  python3 plot_results_three_runs.py
  python3 plot_results_three_runs.py --base-dir "final_scheduler/Task 4.4/QPS_5"
  python3 plot_results_three_runs.py --runs "path/to/Run 1" "path/to/Run 2" "path/to/Run 3"
"""

from __future__ import annotations

import argparse
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.gridspec import GridSpec


# -----------------------------
# CONFIGURATION
# -----------------------------
DEFAULT_BASE_DIR = Path("final_scheduler/Task 4.4/QPS_5")
DEFAULT_RUN_NAMES = ["Run 1", "Run 2", "Run 3"]
INTERVAL_SECONDS = 15
SLO_P95_MS = 0.8
MEMCACHED_JOB_NAME = "memcached"

# Template colours from main.tex.
JOB_COLOR_MAP = {
    "barnes": "#AACCCA",
    "blackscholes": "#CCA000",
    "canneal": "#CCCCAA",
    "freqmine": "#0CCA00",
    "radix": "#00CCA0",
    "streamcluster": "#CCACCA",
    "vips": "#CC0A00",
    "memcached": "#777777",
    "paused": "#FFFFFF",
}

JOB_MARKER_MAP = {
    "blackscholes": "o",
    "canneal": "s",
    "freqmine": "^",
    "radix": "D",
    "streamcluster": "P",
    "vips": "X",
    "barnes": "*",
    "memcached": "o",
}

CPU_LINESTYLES = ["-", "--", "-.", ":"]


@dataclass
class JobState:
    events: list[dict] = field(default_factory=list)
    end: datetime | None = None
    pause_intervals: list[tuple[datetime, datetime]] = field(default_factory=list)
    _pause_start: datetime | None = None
    _last_cores: list[int] = field(default_factory=list)


# -----------------------------
# TIME / PARSING HELPERS
# -----------------------------
def parse_datetime(ts: str) -> datetime:
    """Parse timestamps written by the scheduler logs as UTC datetimes."""
    ts = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_duration(seconds: float | int | None) -> str:
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return "n/a"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def parse_core_list(core_text: str) -> list[int]:
    return [int(c) for c in re.findall(r"\d+", core_text)]


def read_text(path: Path, required: bool = True) -> str:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing required log file: {path}")
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


# -----------------------------
# MEMCACHED MEASUREMENT LOG
# -----------------------------
def parse_measure_log(measure_log: str) -> tuple[pd.DataFrame, datetime | None, datetime | None]:
    start_ts = None
    end_ts = None

    for line in measure_log.splitlines():
        if "Timestamp start" in line:
            start_ts = int(re.findall(r"\d+", line)[-1])
        elif "Timestamp end" in line:
            end_ts = int(re.findall(r"\d+", line)[-1])

    if start_ts is None or end_ts is None:
        raise ValueError("Missing 'Timestamp start' or 'Timestamp end' in measure_log.txt")

    measure_start = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
    measure_end = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)

    rows = []
    interval_idx = 0

    for line in measure_log.splitlines():
        if not line.strip().startswith("read"):
            continue

        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", line)]
        if len(nums) < 12:
            continue

        # Matches the original script's memtier summary parsing.
        qps = nums[-2]
        p95_ms = nums[-6]
        t = datetime.fromtimestamp(
            (start_ts + interval_idx * INTERVAL_SECONDS * 1000) / 1000,
            tz=timezone.utc,
        )

        rows.append({"time": t, "qps": qps, "p95_ms": p95_ms})
        interval_idx += 1

    df = pd.DataFrame(rows, columns=["time", "qps", "p95_ms"])
    return df, measure_start, measure_end


# -----------------------------
# CPU LOG
# -----------------------------
def parse_cpu_log(cpu_log: str) -> pd.DataFrame:
    """
    Parse common CPU log formats into columns: time, core, cpu.

    Supported examples:
      2024-01-01T00:00:00,0,78.2
      2024-01-01T00:00:00,cpu0,78.2
      2024-01-01T00:00:00,78.2                  # aggregate only
      2024-01-01T00:00:00,cpu0=78.2,cpu1=63.1
      2024-01-01T00:00:00 CPU0 78.2 CPU1 63.1
    """
    rows = []

    for raw in cpu_log.splitlines():
        line = raw.strip()
        if not line:
            continue

        # Split off timestamp first. The timestamp is assumed to be the first ISO-looking token.
        m_ts = re.match(r"(\d{4}-\d{2}-\d{2}T[^,\s]+)", line)
        if not m_ts:
            continue

        try:
            t = parse_datetime(m_ts.group(1))
        except Exception:
            continue

        rest = line[m_ts.end():].strip(" ,")
        if not rest:
            continue

        # key=value / CPU0 10.2 style entries.
        keyed = re.findall(r"(?:cpu|core)[_\s-]*(\d+)\s*[:=]?\s*([-+]?\d*\.?\d+)", rest, flags=re.I)
        if keyed:
            for core, value in keyed:
                rows.append({"time": t, "core": int(core), "cpu": float(value)})
            continue

        parts = [p.strip() for p in rest.split(",") if p.strip()]

        if len(parts) >= 2:
            # timestamp,core,value
            core_match = re.search(r"\d+", parts[0])
            val_match = re.search(r"[-+]?\d*\.?\d+", parts[1])
            if core_match and val_match:
                rows.append({"time": t, "core": int(core_match.group(0)), "cpu": float(val_match.group(0))})
                continue

            # timestamp,value0,value1,value2,...
            numeric_parts = []
            for p in parts:
                try:
                    numeric_parts.append(float(p))
                except ValueError:
                    numeric_parts = []
                    break
            if len(numeric_parts) > 1:
                for core, value in enumerate(numeric_parts):
                    rows.append({"time": t, "core": core, "cpu": value})
                continue

        # timestamp,value aggregate fallback.
        val_match = re.search(r"[-+]?\d*\.?\d+", rest)
        if val_match:
            rows.append({"time": t, "core": "total", "cpu": float(val_match.group(0))})

    cpu_df = pd.DataFrame(rows, columns=["time", "core", "cpu"])
    if cpu_df.empty:
        return cpu_df

    # Smooth each core independently to improve readability without hiding trends.
    return (
        cpu_df.set_index("time")
        .groupby("core", group_keys=False)
        .resample("5s")
        .mean(numeric_only=True)
        .reset_index()
    )


# -----------------------------
# SCHEDULER LOG
# -----------------------------
def parse_jobs_log(jobs_log: str, measure_end: datetime | None) -> tuple[dict[str, JobState], datetime | None, datetime | None]:
    # Original log format examples:
    #   2024-... start blackscholes [1, 2] 2
    #   2024-... update_cores blackscholes [2]
    #   2024-... end blackscholes
    start_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.+-]+)\s+start\s+(\w+)\s+\[([^\]]*)\](?:\s+\d+)?")
    end_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.+-]+)\s+end\s+(\w+)")
    update_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.+-]+)\s+update_cores\s+(\w+)\s+\[([^\]]*)\]")
    pause_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.+-]+).*\b(?:pause|paused)\b\s+(\w+)", re.I)
    unpause_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.+-]+).*\b(?:unpause|unpaused|resume|resumed)\b\s+(\w+)", re.I)

    scheduler_start = None
    scheduler_end = None
    jobs: dict[str, JobState] = defaultdict(JobState)

    for line in jobs_log.splitlines():
        if "start scheduler" in line:
            scheduler_start = parse_datetime(line.split(" start scheduler")[0])
            continue
        if "end scheduler" in line:
            scheduler_end = parse_datetime(line.split(" end scheduler")[0])
            continue

        m = start_re.search(line)
        if m:
            t, job, cores = m.groups()
            dt = parse_datetime(t)
            core_list = parse_core_list(cores)
            jobs[job].events.append({"time": dt, "cores": core_list, "state": "running"})
            jobs[job]._last_cores = core_list
            continue

        m = update_re.search(line)
        if m:
            t, job, cores = m.groups()
            dt = parse_datetime(t)
            core_list = parse_core_list(cores)
            state = "paused" if jobs[job]._pause_start else "running"
            jobs[job].events.append({"time": dt, "cores": core_list, "state": state})
            jobs[job]._last_cores = core_list
            continue

        m = unpause_re.search(line)
        if m:
            t, job = m.groups()
            dt = parse_datetime(t)
            if jobs[job]._pause_start is not None:
                jobs[job].pause_intervals.append((jobs[job]._pause_start, dt))
                jobs[job]._pause_start = None
            jobs[job].events.append({"time": dt, "cores": jobs[job]._last_cores, "state": "running"})
            continue

        m = pause_re.search(line)
        if m:
            t, job = m.groups()
            # The unpause regex also contains 'pause'; it was intentionally checked first.
            dt = parse_datetime(t)
            if jobs[job]._pause_start is None:
                jobs[job]._pause_start = dt
            jobs[job].events.append({"time": dt, "cores": jobs[job]._last_cores, "state": "paused"})
            continue

        m = end_re.search(line)
        if m:
            t, job = m.groups()
            dt = parse_datetime(t)
            jobs[job].end = dt
            if jobs[job]._pause_start is not None:
                jobs[job].pause_intervals.append((jobs[job]._pause_start, dt))
                jobs[job]._pause_start = None
            continue

    if MEMCACHED_JOB_NAME in jobs and jobs[MEMCACHED_JOB_NAME].events and measure_end is not None:
        jobs[MEMCACHED_JOB_NAME].end = measure_end

    return dict(jobs), scheduler_start, scheduler_end


def build_segments(jobs: dict[str, JobState]) -> tuple[list[dict], list[dict]]:
    running_segments = []
    paused_segments = []

    for job, info in jobs.items():
        events = sorted(info.events, key=lambda x: x["time"])
        if not events:
            continue

        for idx, event in enumerate(events):
            start = event["time"]
            end = events[idx + 1]["time"] if idx + 1 < len(events) else info.end
            if end is None or end <= start:
                continue

            target = paused_segments if event.get("state") == "paused" else running_segments
            for core in event.get("cores", []):
                target.append({"job": job, "core": core, "start": start, "end": end})

        # Also add explicit pause intervals because some logs only include pause/unpause events without state changes.
        for start, end in info.pause_intervals:
            if end <= start:
                continue
            cores = info._last_cores or [0]
            for core in cores:
                paused_segments.append({"job": job, "core": core, "start": start, "end": end})

    return merge_segments(running_segments), merge_segments(paused_segments)


def merge_segments(segments: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for seg in segments:
        grouped[(seg["job"], seg["core"])].append(seg)

    merged_all = []
    for (_job, _core), segs in grouped.items():
        segs.sort(key=lambda s: s["start"])
        merged = [segs[0].copy()]
        for seg in segs[1:]:
            prev = merged[-1]
            if prev["end"] == seg["start"]:
                prev["end"] = seg["end"]
            else:
                merged.append(seg.copy())
        merged_all.extend(merged)

    return sorted(merged_all, key=lambda s: (s["core"], s["start"], s["job"]))


# -----------------------------
# METRICS
# -----------------------------
def batch_jobs(jobs: dict[str, JobState]) -> list[str]:
    return sorted(j for j in jobs if j != MEMCACHED_JOB_NAME and jobs[j].events and jobs[j].end is not None)


def job_runtime_seconds(info: JobState) -> float | None:
    if not info.events or info.end is None:
        return None
    start = min(e["time"] for e in info.events)
    runtime = (info.end - start).total_seconds()
    for pause_start, pause_end in info.pause_intervals:
        overlap_start = max(start, pause_start)
        overlap_end = min(info.end, pause_end)
        if overlap_end > overlap_start:
            runtime -= (overlap_end - overlap_start).total_seconds()
    return max(runtime, 0.0)


def batch_window(jobs: dict[str, JobState]) -> tuple[datetime | None, datetime | None]:
    names = batch_jobs(jobs)
    if not names:
        return None, None
    start = min(min(e["time"] for e in jobs[j].events) for j in names)
    end = max(jobs[j].end for j in names if jobs[j].end is not None)
    return start, end


def slo_violation_ratio(df: pd.DataFrame, start: datetime | None, end: datetime | None) -> float:
    if df.empty or start is None or end is None:
        return float("nan")
    in_window = df[(df["time"] >= start) & (df["time"] <= end)]
    if in_window.empty:
        return float("nan")
    return float((in_window["p95_ms"] > SLO_P95_MS).sum() / len(in_window))


# -----------------------------
# PLOTTING
# -----------------------------
def elapsed_formatter_factory(xmin: datetime):
    def elapsed_formatter(x, _pos):
        dt = mdates.num2date(x, tz=timezone.utc)
        elapsed = max(0, int((dt - xmin).total_seconds()))
        hours, rem = divmod(elapsed, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"

    return elapsed_formatter


def step_xy(df: pd.DataFrame, value_col: str) -> tuple[list[datetime], list[float]]:
    xs, ys = [], []
    if df.empty:
        return xs, ys
    for idx, row in df.iterrows():
        t_start = row["time"]
        t_end = df.iloc[idx + 1]["time"] if idx + 1 < len(df) else t_start + timedelta(seconds=INTERVAL_SECONDS)
        xs.extend([t_start, t_end])
        ys.extend([row[value_col], row[value_col]])
    return xs, ys


def plot_run(
    run_dir: Path,
    run_label: str,
    df: pd.DataFrame,
    cpu_df: pd.DataFrame,
    jobs: dict[str, JobState],
    running_segments: list[dict],
    paused_segments: list[dict],
    metrics: dict,
) -> Path:
    all_times: list[datetime] = []
    if not df.empty:
        all_times.extend(df["time"].tolist())
    if not cpu_df.empty:
        all_times.extend(cpu_df["time"].tolist())
    for seg in running_segments + paused_segments:
        all_times.extend([seg["start"], seg["end"]])

    if not all_times:
        raise ValueError(f"No timestamps found for {run_dir}")

    xmin, xmax = min(all_times), max(all_times)

    fig = plt.figure(figsize=(18, 12))
    gs = GridSpec(3, 1, height_ratios=[1.3, 1.0, 1.0], hspace=0.18)
    ax_alloc = fig.add_subplot(gs[0])
    ax_qps = fig.add_subplot(gs[1], sharex=ax_alloc)
    ax_cpu = fig.add_subplot(gs[2], sharex=ax_alloc)

    for ax in (ax_alloc, ax_qps, ax_cpu):
        ax.set_xlim(xmin, xmax)
        ax.margins(x=0)
        ax.grid(True, axis="x", linestyle=":", alpha=0.45)

    plt.setp(ax_alloc.get_xticklabels(), visible=False)
    plt.setp(ax_qps.get_xticklabels(), visible=False)

    ax_timer = ax_alloc.secondary_xaxis("top")
    ax_timer.xaxis.set_major_formatter(mpl.ticker.FuncFormatter(elapsed_formatter_factory(xmin)))
    ax_timer.set_xlabel("Elapsed time from execution start")

    # 1) Allocation timeline.
    cores = sorted({seg["core"] for seg in running_segments + paused_segments})
    job_names = sorted({seg["job"] for seg in running_segments + paused_segments})
    job_colors = {job: JOB_COLOR_MAP.get(job, "#333333") for job in job_names}

    for seg in running_segments:
        left = mdates.date2num(seg["start"])
        width = (seg["end"] - seg["start"]).total_seconds() / 86400
        ax_alloc.barh(
            seg["core"],
            width,
            left=left,
            height=0.55,
            color=job_colors.get(seg["job"], "#333333"),
            edgecolor="black",
            linewidth=0.45,
            alpha=0.9,
        )
        duration = (seg["end"] - seg["start"]).total_seconds()
        if duration >= 20:
            ax_alloc.text(
                seg["start"] + (seg["end"] - seg["start"]) / 2,
                seg["core"],
                seg["job"],
                ha="center",
                va="center",
                fontsize=8,
                clip_on=True,
            )

    for seg in paused_segments:
        left = mdates.date2num(seg["start"])
        width = (seg["end"] - seg["start"]).total_seconds() / 86400
        ax_alloc.barh(
            seg["core"],
            width,
            left=left,
            height=0.68,
            facecolor="none",
            edgecolor="black",
            linewidth=0.6,
            hatch="////",
            alpha=0.9,
        )

    ax_alloc.set_title(f"{run_label}: per-core workload allocation")
    ax_alloc.set_ylabel("Core")
    if cores:
        ax_alloc.set_yticks(cores)
    ax_alloc.grid(True, axis="y", linestyle=":", alpha=0.25)

    handles = [mpl.patches.Patch(facecolor=job_colors[j], edgecolor="black", label=j) for j in job_names]
    if paused_segments:
        handles.append(mpl.patches.Patch(facecolor="white", edgecolor="black", hatch="////", label="paused"))
    ax_alloc.legend(handles=handles, title="Workload", loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)

    # 2) QPS + p95 latency.
    qps_x, qps_y = step_xy(df, "qps")
    p95_x, p95_y = step_xy(df, "p95_ms")

    qps_line, = ax_qps.step(qps_x, qps_y, where="post", linestyle="--", linewidth=2, label="QPS")
    ax_qps.set_title("memcached throughput and p95 latency")
    ax_qps.set_ylabel("QPS")
    ax_qps.tick_params(axis="y")
    ax_qps.grid(True, linestyle=":", alpha=0.45)

    ax_latency = ax_qps.twinx()
    p95_line, = ax_latency.step(p95_x, p95_y, where="post", linewidth=2, label="p95 latency")
    slo_line = ax_latency.axhline(SLO_P95_MS, linestyle=":", linewidth=1.8, alpha=0.9, label=f"SLO = {SLO_P95_MS:g} ms")
    ax_latency.set_ylabel("p95 latency (ms)")

    ax_qps.legend(handles=[qps_line, p95_line, slo_line], loc="upper left", bbox_to_anchor=(1.01, 1.0), title="memcached", frameon=True)

    # 3) CPU utilisation per core.
    if not cpu_df.empty:
        cpu_cores = sorted(cpu_df["core"].unique(), key=lambda x: (str(type(x)), x))
        for idx, core in enumerate(cpu_cores):
            cdf = cpu_df[cpu_df["core"] == core]
            ax_cpu.plot(
                cdf["time"],
                cdf["cpu"],
                linewidth=1.6,
                linestyle=CPU_LINESTYLES[idx % len(CPU_LINESTYLES)],
                label=f"core {core}",
            )
        ax_cpu.legend(title="CPU", loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)
    else:
        ax_cpu.text(0.5, 0.5, "No CPU data found", ha="center", va="center", transform=ax_cpu.transAxes)

    ax_cpu.set_title("CPU utilisation per core")
    ax_cpu.set_ylabel("CPU utilisation (%)")
    ax_cpu.set_xlabel("Absolute time")
    ax_cpu.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=timezone.utc))
    fig.autofmt_xdate(rotation=20)

    text = (
        f"Makespan: {fmt_duration(metrics['makespan_s'])}\n"
        f"SLO violation ratio: {metrics['slo_violation_ratio']:.3f}"
        if not math.isnan(metrics["slo_violation_ratio"])
        else f"Makespan: {fmt_duration(metrics['makespan_s'])}\nSLO violation ratio: n/a"
    )
    ax_alloc.text(
        0.985,
        0.04,
        text,
        transform=ax_alloc.transAxes,
        ha="right",
        va="bottom",
        fontsize=10,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="0.7", alpha=0.92),
    )

    plt.tight_layout()
    output_file = run_dir / "analysis_plot_three_subplots.png"
    fig.savefig(output_file, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return output_file


# -----------------------------
# RUN WORKFLOW
# -----------------------------
def process_run(run_dir: Path, run_idx: int) -> tuple[dict, list[dict], Path]:
    measure_log = read_text(run_dir / "measure_log.txt")
    jobs_log = read_text(run_dir / "jobs_log.txt")
    cpu_log = read_text(run_dir / "cpu_usage.log", required=False)

    df, _measure_start, measure_end = parse_measure_log(measure_log)
    cpu_df = parse_cpu_log(cpu_log)
    jobs, scheduler_start, scheduler_end = parse_jobs_log(jobs_log, measure_end)
    running_segments, paused_segments = build_segments(jobs)

    first_batch_start, last_batch_end = batch_window(jobs)
    makespan_s = None if first_batch_start is None or last_batch_end is None else (last_batch_end - first_batch_start).total_seconds()
    violation_ratio = slo_violation_ratio(df, first_batch_start, last_batch_end)

    metrics = {
        "run": run_idx,
        "run_dir": str(run_dir),
        "scheduler_runtime_s": None if scheduler_start is None or scheduler_end is None else (scheduler_end - scheduler_start).total_seconds(),
        "first_batch_start": first_batch_start,
        "last_batch_end": last_batch_end,
        "makespan_s": makespan_s,
        "slo_violation_ratio": violation_ratio,
        "slo_threshold_ms": SLO_P95_MS,
    }

    job_rows = []
    for job in batch_jobs(jobs):
        runtime = job_runtime_seconds(jobs[job])
        start = min(e["time"] for e in jobs[job].events)
        end = jobs[job].end
        paused_s = sum((b - a).total_seconds() for a, b in jobs[job].pause_intervals)
        job_rows.append(
            {
                "run": run_idx,
                "run_dir": str(run_dir),
                "job": job,
                "start": start,
                "end": end,
                "paused_s": paused_s,
                "runtime_excluding_pauses_s": runtime,
            }
        )

    output_file = plot_run(
        run_dir=run_dir,
        run_label=f"Run {run_idx}",
        df=df,
        cpu_df=cpu_df,
        jobs=jobs,
        running_segments=running_segments,
        paused_segments=paused_segments,
        metrics=metrics,
    )
    return metrics, job_rows, output_file


def write_summaries(output_dir: Path, per_run_rows: list[dict], job_rows: list[dict]) -> None:
    per_run_df = pd.DataFrame(per_run_rows)
    jobs_df = pd.DataFrame(job_rows)

    per_run_df.to_csv(output_dir / "per_run_metrics.csv", index=False)
    jobs_df.to_csv(output_dir / "per_job_runtime_by_run.csv", index=False)

    if not jobs_df.empty:
        summary = (
            jobs_df.groupby("job", as_index=False)["runtime_excluding_pauses_s"]
            .agg(mean_s="mean", std_s="std")
            .sort_values("job")
        )
        summary["mean_fmt"] = summary["mean_s"].map(fmt_duration)
        summary["std_fmt"] = summary["std_s"].map(lambda x: fmt_duration(0 if pd.isna(x) else x))
        summary.to_csv(output_dir / "per_job_runtime_summary.csv", index=False)

    if not per_run_df.empty:
        makespan = pd.DataFrame(
            [
                {
                    "metric": "makespan",
                    "mean_s": per_run_df["makespan_s"].mean(),
                    "std_s": per_run_df["makespan_s"].std(),
                    "mean_fmt": fmt_duration(per_run_df["makespan_s"].mean()),
                    "std_fmt": fmt_duration(0 if pd.isna(per_run_df["makespan_s"].std()) else per_run_df["makespan_s"].std()),
                }
            ]
        )
        makespan.to_csv(output_dir / "makespan_summary.csv", index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot and summarise three scheduler/memcached runs.")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR, help="Directory containing Run 1, Run 2, Run 3.")
    parser.add_argument("--runs", nargs="*", type=Path, help="Explicit run directories. Overrides --base-dir.")
    parser.add_argument("--output-dir", type=Path, help="Directory for summary CSVs. Defaults to the common parent/base dir.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dirs = args.runs if args.runs else [args.base_dir / name for name in DEFAULT_RUN_NAMES]
    output_dir = args.output_dir or (args.base_dir if not args.runs else run_dirs[0].parent)
    output_dir.mkdir(parents=True, exist_ok=True)

    per_run_rows = []
    all_job_rows = []
    output_files = []

    for idx, run_dir in enumerate(run_dirs, start=1):
        metrics, job_rows, output_file = process_run(run_dir, idx)
        per_run_rows.append(metrics)
        all_job_rows.extend(job_rows)
        output_files.append(output_file)
        print(f"Run {idx}: wrote {output_file}")
        print(f"  makespan={fmt_duration(metrics['makespan_s'])}, SLO violation ratio={metrics['slo_violation_ratio']:.3f}")

    write_summaries(output_dir, per_run_rows, all_job_rows)
    print(f"Wrote summary CSVs to {output_dir}")


if __name__ == "__main__":
    main()
