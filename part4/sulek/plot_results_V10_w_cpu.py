import re
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
import matplotlib as mpl
from collections import defaultdict

# -----------------------------
# CONFIG
# -----------------------------
POLICY_DIR = "final_scheduler/Task 4.3/Run 1"
# POLICY_DIR = "final_scheduler/Task 4.3/Run 2"
# POLICY_DIR = "final_scheduler/Task 4.3/Run 3"
INTERVAL_SECONDS = 15

LOG_INTERVAL_FILE = f"{POLICY_DIR}/measure_log.txt"
LOG_SCHED_FILE = f"{POLICY_DIR}/jobs_log.txt"
LOG_CPU_FILE = f"{POLICY_DIR}/cpu_usage.log"

JOB_COLOR_MAP = {
    "blackscholes": "#CCA000",
    "canneal": "#CCCCAA",
    "freqmine": "#0CCA00",
    "radix": "#03614C",
    "streamcluster": "#CCACCA",
    "vips": "#CC0A00",
    "barnes": "#64CFC8",
    "memcached": "#7f7f7f"
}

# --- strong distinct palette for CPU cores ---
CPU_CORE_COLOR_MAP = {
    "core0": "#2f25b8",  
    "core1": "#2ca02c",  
    "core2": "#ff7f0e",  
    "core3": "#e41212",  
}

marker_map = {
    "blackscholes": "o",
    "canneal": "s",
    "freqmine": "^",
    "radix": "D",
    "streamcluster": "P",
    "vips": "X",
    "barnes": "*",
    "memcached": "o"
}

# -----------------------------
# LOAD FILES
# -----------------------------
with open(LOG_INTERVAL_FILE, "r", encoding="utf-8", errors="ignore") as f:
    measure_log = f.read()

with open(LOG_SCHED_FILE, "r", encoding="utf-8", errors="ignore") as f:
    jobs_log = f.read()

with open(LOG_CPU_FILE, "r", encoding="utf-8", errors="ignore") as f:
    cpu_log = f.read()

# -----------------------------
# SCHEDULER TOTAL RUNTIME
# -----------------------------
scheduler_start = None
scheduler_end = None

for line in jobs_log.splitlines():
    if "start scheduler" in line:
        ts = line.split(" start scheduler")[0].strip()
        scheduler_start = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

    elif "end scheduler" in line:
        ts = line.split(" end scheduler")[0].strip()
        scheduler_end = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

if scheduler_start and scheduler_end:
    total_runtime = scheduler_end - scheduler_start
else:
    total_runtime = None

# -----------------------------
# TIMESTAMP RANGE
# -----------------------------
start_ts = None
end_ts = None

for line in measure_log.splitlines():
    if "Timestamp start" in line:
        start_ts = int(line.split(":")[1].strip())
    if "Timestamp end" in line:
        end_ts = int(line.split(":")[1].strip())

measure_end = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)

# -----------------------------
# QPS + P95
# -----------------------------
rows = []
idx = 0

for line in measure_log.splitlines():
    if line.startswith("read"):
        nums = re.findall(r"[\d.]+", line)
        if len(nums) < 12:
            continue

        qps = float(nums[-2])
        p95 = float(nums[-6])

        t = datetime.fromtimestamp(
            (start_ts + idx * INTERVAL_SECONDS * 1000) / 1000,
            tz=timezone.utc
        )

        rows.append([t, qps, p95])
        idx += 1

df = pd.DataFrame(rows, columns=["time", "qps", "p95"])

# -----------------------------
# CPU PARSE
# -----------------------------
cpu_rows = []

for line in cpu_log.splitlines():
    parts = line.strip().split(",")
    if len(parts) < 2:
        continue

    try:
        ts = datetime.fromisoformat(parts[0]).replace(tzinfo=timezone.utc)
        row = {"time": ts}

        for item in parts[2:]:
            if "=" in item:
                k, v = item.split("=")
                row[k.strip()] = float(v)

        cpu_rows.append(row)
    except:
        continue

cpu_df = pd.DataFrame(cpu_rows)

if not cpu_df.empty:
    cpu_df = cpu_df.set_index("time").resample("5s").mean().reset_index()

# -----------------------------
# JOB LOG PARSE
# -----------------------------
job_start_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+start\s+(\w+)\s+\[([^\]]+)\]")
job_end_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+end\s+(\w+)")
update_re = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+update_cores\s+(\w+)\s+\[([^\]]+)\]")

jobs = {"memcached": {"events": [], "end": None}}

for line in jobs_log.splitlines():

    m = job_start_re.search(line)
    if m:
        t, job, cores = m.groups()
        cores = [int(c.strip()) for c in cores.split(",")]
        jobs[job] = {
            "events": [{
                "time": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                "cores": cores
            }],
            "end": None
        }

    m = update_re.search(line)
    if m:
        t, job, cores = m.groups()
        cores = [int(c.strip()) for c in cores.split(",")]
        if job in jobs:
            jobs[job]["events"].append({
                "time": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                "cores": cores
            })

    m = job_end_re.search(line)
    if m:
        t, job = m.groups()
        if job in jobs:
            jobs[job]["end"] = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)

jobs["memcached"]["end"] = measure_end

# -----------------------------
# SEGMENTS
# -----------------------------
segments = []

for job, info in jobs.items():
    events = sorted(info["events"], key=lambda x: x["time"])

    for i in range(len(events)):
        start = events[i]["time"]
        end = events[i + 1]["time"] if i + 1 < len(events) else info["end"]

        for core in events[i]["cores"]:
            segments.append({
                "job": job,
                "core": core,
                "start": start,
                "end": end
            })

grouped = defaultdict(list)

for s in segments:
    grouped[(s["job"], s["core"])].append(s)

job_segments = []
for _, segs in grouped.items():
    segs.sort(key=lambda x: x["start"])
    merged = [segs[0]]

    for s in segs[1:]:
        if merged[-1]["end"] == s["start"]:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)

    job_segments.extend(merged)

# -----------------------------
# ELAPSED TIME
# -----------------------------
xmin = df["time"].iloc[0]
x_start = df["time"].iloc[0]
x_end = df["time"].iloc[-1]

def elapsed(x, pos):
    dt = mdates.num2date(x, tz=timezone.utc)
    delta = int((dt - xmin).total_seconds())
    m, s = divmod(delta, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

formatter = mpl.ticker.FuncFormatter(elapsed)

# -----------------------------
# FIGURE
# -----------------------------
fig = plt.figure(figsize=(20, 12))
gs = GridSpec(3, 1, height_ratios=[1.2, 1, 1], hspace=0.25)

ax_gantt = fig.add_subplot(gs[0])
ax_metrics = fig.add_subplot(gs[1], sharex=ax_gantt)
ax_cpu = fig.add_subplot(gs[2], sharex=ax_gantt)

# -----------------------------
# 1) GANTT + MARKERS + LEGEND
# -----------------------------
job_names = sorted(set(s["job"] for s in job_segments))

for s in job_segments:
    left = mdates.date2num(s["start"])
    width = (s["end"] - s["start"]).total_seconds() / 86400

    color = JOB_COLOR_MAP.get(s["job"], "#333")

    ax_gantt.barh(
        s["core"],
        width,
        left=left,
        height=0.45,
        color=color,
        edgecolor=color,
        alpha=0.85
    )

    # --- restored markers ---
    mid = s["start"] + (s["end"] - s["start"]) / 2
    ax_gantt.plot(
        mid,
        s["core"],
        marker=marker_map.get(s["job"], "o"),
        markersize=4,
        color=color,
        markerfacecolor=color,
        markeredgecolor=color,
        linestyle="None",
        alpha=0.9
    )

ax_gantt.set_title("Core Allocation Timeline")
ax_gantt.set_ylabel("Core")
ax_gantt.grid(True, axis="x", linestyle=":", alpha=0.4)
ax_gantt.xaxis.set_major_formatter(formatter)

gantt_handles = [
    mpl.patches.Patch(color=JOB_COLOR_MAP[j], label=j)
    for j in job_names
]

ax_gantt.legend(
    handles=gantt_handles,
    loc="upper right",
    frameon=True,
    title="Jobs"
)

# -----------------------------
# TOTAL RUNTIME BOX
# -----------------------------
if total_runtime is not None:
    total_seconds = int(total_runtime.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        runtime_str = f"{hours:d}:{minutes:02d}:{seconds:02d}"
    else:
        runtime_str = f"{minutes:d}:{seconds:02d}"

    ax_gantt.text(
        0.99, 0.02,
        f"Total Job Runtime: {runtime_str}",
        transform=ax_gantt.transAxes,
        ha="right",
        va="bottom",
        fontsize=10,
        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="white",
            edgecolor="0.7",
            alpha=0.9
        )
    )

# -----------------------------
# 2) QPS + P95
# -----------------------------
xs, qps, p95 = [], [], []

for i in range(len(df)):
    t0 = df.iloc[i]["time"]
    t1 = df.iloc[i + 1]["time"] if i + 1 < len(df) else t0 + timedelta(seconds=15)

    xs += [t0, t1]
    qps += [df.iloc[i]["qps"]] * 2
    p95 += [df.iloc[i]["p95"]] * 2

ax_metrics.plot(xs, qps, label="QPS", color="green", linestyle="--")
ax_p95 = ax_metrics.twinx()
ax_p95.plot(xs, p95, label="p95", color="red")

ax_p95.axhline(800, linestyle="--", color="red")

ax_metrics.set_title("Throughput & Latency")
ax_metrics.set_ylabel("QPS")
ax_p95.set_ylabel("p95 ms")
ax_metrics.grid(True, linestyle=":", alpha=0.4)
ax_metrics.xaxis.set_major_formatter(formatter)

# collect handles + labels from both axes
handles1, labels1 = ax_metrics.get_legend_handles_labels()
handles2, labels2 = ax_p95.get_legend_handles_labels()

ax_metrics.set_zorder(1)
ax_metrics.patch.set_alpha(0)

ax_p95.set_zorder(2)

leg = ax_p95.legend(
    handles1 + handles2,
    labels1 + labels2,
    loc="upper right",
    frameon=True,
    facecolor="white"
)

leg.set_zorder(1000)

# -----------------------------
# 3) CPU (NO TWINS)
# -----------------------------
if not cpu_df.empty:
    for i, core in enumerate([c for c in cpu_df.columns if c.startswith("core")]):

        color = CPU_CORE_COLOR_MAP.get(core, "#333333")

        ax_cpu.plot(
            cpu_df["time"],
            cpu_df[core],
            color=color,
            label=core
        )

ax_cpu.set_title("Per-Core CPU Utilization")
ax_cpu.set_ylabel("CPU %")
ax_cpu.set_ylim(0, 100)
ax_cpu.grid(True, linestyle=":", alpha=0.4)

ax_cpu.legend(
    loc="upper right",
    ncol=4,
    frameon=True
)

ax_cpu.xaxis.set_major_formatter(formatter)

# -----------------------------
# X AXIS TICK SETUP (ADD THIS)
# -----------------------------
major_locator = mdates.MinuteLocator(interval=5)
minor_locator = mdates.MinuteLocator(interval=1)

for ax in [ax_gantt, ax_metrics, ax_cpu]:
    ax.xaxis.set_major_locator(major_locator)
    ax.xaxis.set_minor_locator(minor_locator)
    ax.set_xlim(df["time"].iloc[0], df["time"].iloc[-1])
    ax.margins(x=0)
    
# -----------------------------
# FINAL
# -----------------------------
fig.subplots_adjust(left=0.07, right=0.95, top=0.93, bottom=0.08)

plt.savefig(f"{POLICY_DIR}/analysis_plot.png", dpi=600, bbox_inches="tight")
plt.show()