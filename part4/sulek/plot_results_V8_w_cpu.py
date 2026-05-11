import re
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
import matplotlib as mpl

# -----------------------------
# FILE INPUTS
# -----------------------------
# POLICY_DIR = "single_job_seq"
# POLICY_DIR = "single_job_2_core_seq"
# POLICY_DIR = "double_job"
# POLICY_DIR = "2_core_memcache_2_core_job"
# POLICY_DIR = "master_run_maybefinal"
POLICY_DIR = "2_3_core_memcache_1_2_core_job_single_seq"
# POLICY_DIR = "4_core_memcache"
# POLICY_DIR = "3_core_memcache"

LOG_AGENT_FILE = f"{POLICY_DIR}/agent_log.txt" 
LOG_INTERVAL_FILE = f"{POLICY_DIR}/measure_log.txt"
LOG_SCHED_FILE = f"{POLICY_DIR}/jobs_log.txt" 

MEMCACHED_CORES = [0,1,2]

# -----------------------------
# LOAD FILES
# -----------------------------
with open(LOG_AGENT_FILE, "r", encoding="utf-8", errors="ignore") as f:
    agent_log = f.read()

with open(LOG_INTERVAL_FILE, "r", encoding="utf-8", errors="ignore") as f:
    measure_log = f.read()

with open(LOG_SCHED_FILE, "r", encoding="utf-8", errors="ignore") as f:
    jobs_log = f.read()


# -----------------------------
# PARSE TOTAL SCHEDULER RUNTIME
# -----------------------------
scheduler_start = None
scheduler_end = None

for line in jobs_log.splitlines():

    if "start scheduler" in line:
        ts = line.split(" start scheduler")[0].strip()

        scheduler_start = datetime.fromisoformat(ts).replace(
            tzinfo=timezone.utc
        )

    elif "end scheduler" in line:
        ts = line.split(" end scheduler")[0].strip()

        scheduler_end = datetime.fromisoformat(ts).replace(
            tzinfo=timezone.utc
        )

if scheduler_start and scheduler_end:
    total_runtime = scheduler_end - scheduler_start
else:
    total_runtime = None

# -----------------------------
# PARSE INTERVAL LOG
# -----------------------------
start_ts = None
end_ts = None

for line in measure_log.splitlines():
    if "Timestamp start" in line:
        start_ts = int(line.split(":")[1].strip())
    if "Timestamp end" in line:
        end_ts = int(line.split(":")[1].strip())

if start_ts is None or end_ts is None:
    raise ValueError("Missing timestamps")

interval_seconds = 15

rows = []
interval_idx = 0

for line in measure_log.splitlines():
    if line.strip().startswith("read"):
        nums = re.findall(r"[\d.]+", line)
        if len(nums) < 12:
            continue

        qps = float(nums[-2])
        p95 = float(nums[-6])

        t = datetime.fromtimestamp(
            (start_ts + interval_idx * interval_seconds * 1000) / 1000,
            tz=timezone.utc
        )

        rows.append([t, qps, p95])
        interval_idx += 1

df = pd.DataFrame(rows, columns=["time", "qps", "p95"])

# -----------------------------
# PARSE SCHEDULER LOG (FIXED)
# -----------------------------
job_start_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+start\s+(\w+)\s+\[([^\]]+)\]\s+(\d+)"
)

job_end_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+end\s+(\w+)"
)

update_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+update_cores\s+(\w+)\s+\[([^\]]+)\]"
)

jobs = {}

for line in jobs_log.splitlines():

    # START
    m = job_start_re.search(line)
    if m:
        t, job, cores, threads = m.groups()

        cores = [int(c.strip()) for c in cores.split(",")]

        jobs[job] = {
            "events": [{
                "time": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                "cores": cores
            }],
            "end": None
        }
        continue

    # UPDATE
    m = update_re.search(line)
    if m:
        t, job, cores = m.groups()

        cores = [int(c.strip()) for c in cores.split(",")]

        if job in jobs:
            jobs[job]["events"].append({
                "time": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                "cores": cores
            })
        continue

    # END
    m = job_end_re.search(line)
    if m:
        t, job = m.groups()
        if job in jobs:
            jobs[job]["end"] = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)

# add memcached full span
jobs["memcached"] = {
    "events": [{
        "time": datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc),
        "cores": MEMCACHED_CORES
    }],
    "end": datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
}

# -----------------------------
# BUILD RAW PER-CORE SEGMENTS
# -----------------------------
raw_segments = []

for job, info in jobs.items():
    events = sorted(info["events"], key=lambda x: x["time"])

    for i in range(len(events)):
        start = events[i]["time"]

        if i < len(events) - 1:
            end = events[i + 1]["time"]
        else:
            end = info["end"] if info["end"] else start + timedelta(seconds=5)

        # create one segment per core
        for core in events[i]["cores"]:
            raw_segments.append({
                "job": job,
                "core": core,
                "start": start,
                "end": end,
            })

# -----------------------------
# MERGE NEIGHBOURING SEGMENTS
# (same job + same core)
# -----------------------------
from collections import defaultdict

grouped = defaultdict(list)

# group by (job, core)
for seg in raw_segments:
    grouped[(seg["job"], seg["core"])].append(seg)

job_segments = []

for (job, core), segs in grouped.items():

    # sort by time
    segs.sort(key=lambda s: s["start"])

    merged = [segs[0]]

    for seg in segs[1:]:

        prev = merged[-1]

        # neighbouring segments
        if prev["end"] == seg["start"]:
            prev["end"] = seg["end"]
        else:
            merged.append(seg)

    job_segments.extend(merged)

# final sort for plotting
job_segments.sort(key=lambda s: (s["core"], s["start"]))

# -----------------------------
# PLOTTING
# -----------------------------
all_times = df["time"].tolist()
for seg in job_segments:
    all_times.append(seg["start"])
    all_times.append(seg["end"])

xmin = min(all_times)
xmax = max(all_times)

fig = plt.figure(figsize=(18, 10))

gs = GridSpec(
    2, 1,
    height_ratios=[1, 1],
    hspace=0.25
)

ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1], sharex=ax1)

ax1.set_xlim(xmin, xmax)
ax2.set_xlim(xmin, xmax)

#ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
# Absolute time on the bottom shared x-axis
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
ax2.set_xlabel("Absolute time")

# Hide duplicate absolute labels on the upper plot
plt.setp(ax1.get_xticklabels(), visible=False)

# Elapsed-time formatter, measured from xmin
def elapsed_formatter(x, pos):
    dt = mdates.num2date(x, tz=timezone.utc)
    elapsed = dt - xmin

    total_seconds = int(elapsed.total_seconds())
    if total_seconds < 0:
        total_seconds = 0

    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"

# Timer axis above the top plot
ax_timer = ax1.secondary_xaxis("top")
ax_timer.xaxis.set_major_formatter(mpl.ticker.FuncFormatter(elapsed_formatter))
ax_timer.set_xlabel("Elapsed time from start")

# -----------------------------
# TOP GRAPH
# -----------------------------
ax1.set_title("Latency and Throughput Timeline", fontsize=14, fontweight="bold")

xs, qps_vals, p95_vals = [], [], []

COLOR_QPS = "#16B416"
COLOR_P95 = "#DD0E0E"

for i in range(len(df)):
    t_start = df.iloc[i]["time"]
    t_end = df.iloc[i + 1]["time"] if i < len(df) - 1 else t_start + timedelta(seconds=interval_seconds)

    xs.extend([t_start, t_end])
    qps_vals.extend([df.iloc[i]["qps"]] * 2)
    p95_vals.extend([df.iloc[i]["p95"]] * 2)

qps_line, = ax1.step(
    xs,
    qps_vals,
    where="post",
    linestyle="--",
    linewidth=2,
    label="QPS",
    color=COLOR_QPS
)

ax1.set_ylabel("QPS", color=COLOR_QPS)
ax1.tick_params(axis="y", labelcolor=COLOR_QPS)
ax1.grid(True, linestyle=":", alpha=0.5)

ax1b = ax1.twinx()

p95_line, = ax1b.step(
    xs,
    p95_vals,
    where="post",
    linewidth=2,
    label="p95 latency",
    color=COLOR_P95
)

slo_line = ax1b.axhline(
    800,
    linestyle="--",
    linewidth=1.5,
    alpha=0.6,
    color=COLOR_P95,
    label="800 ms SLO"
)

ax1b.set_ylabel("p95 latency (ms)", color=COLOR_P95)
ax1b.tick_params(axis="y", labelcolor=COLOR_P95)

# Metric legend outside the top plot
ax1.legend(
    handles=[qps_line, p95_line, slo_line],
    loc="upper left",
    bbox_to_anchor=(1.01, 1.0),
    frameon=True,
    title="Metrics"
)

# -----------------------------
# GANTT
# -----------------------------
job_names = sorted(set(seg["job"] for seg in job_segments))
cmap = mpl.colormaps["tab10"]
job_color = {job: cmap(i % 10) for i, job in enumerate(job_names)}

for seg in job_segments:
    left = mdates.date2num(seg["start"])
    width = (seg["end"] - seg["start"]).total_seconds() / 86400

    ax2.barh(
        seg["core"],
        width,
        left=left,
        height=0.42,
        color=job_color[seg["job"]],
        edgecolor="black",
        linewidth=0.6
    )

ax2.set_title("Core Allocation Timeline", fontsize=14, fontweight="bold")
ax2.set_xlabel("Absolute time")
ax2.set_ylabel("Core")
ax2.set_yticks(sorted(set(seg["core"] for seg in job_segments)))
ax2.grid(True, axis="x", linestyle=":", alpha=0.5)

# Job legend instead of labels inside bars
job_handles = [
    mpl.patches.Patch(
        facecolor=job_color[job],
        edgecolor="black",
        label=job
    )
    for job in job_names
]

ax2.legend(
    handles=job_handles,
    title="Jobs",
    loc="upper left",
    bbox_to_anchor=(1.01, 1.0),
    frameon=True
)

# -----------------------------
# TOTAL RUNTIME BOX
# -----------------------------
if total_runtime is not None:
    total_seconds = int(total_runtime.total_seconds())
else:
    total_seconds = 0

hours, remainder = divmod(total_seconds, 3600)
minutes, seconds = divmod(remainder, 60)

if hours > 0:
    runtime_str = f"{hours:d}:{minutes:02d}:{seconds:02d}"
else:
    runtime_str = f"{minutes:d}:{seconds:02d}"

ax2.text(
    0.985,
    0.03,
    f"Total runtime: {runtime_str}",
    transform=ax2.transAxes,
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

plt.tight_layout()

output_file = f"{POLICY_DIR}/analysis_plot.png"
plt.savefig(output_file, dpi=300, bbox_inches="tight")

print(f"Plot saved to {output_file}")
plt.show()