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
# POLICY_DIR = "3_core_memcache_1_core_job"
POLICY_DIR = "2_3_core_memcache_1_2_core_job_single_seq"
# POLICY_DIR = "4_core_memcache"
# POLICY_DIR = "3_core_memcache"

LOG_AGENT_FILE = f"{POLICY_DIR}/agent_log.txt" 
LOG_INTERVAL_FILE = f"{POLICY_DIR}/measure_log.txt"
LOG_SCHED_FILE = f"{POLICY_DIR}/jobs_log.txt" 
LOG_CPU_FILE = f"{POLICY_DIR}/cpu_usage.log"

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

with open(LOG_CPU_FILE, "r", encoding="utf-8", errors="ignore") as f:
    cpu_log = f.read()

# -----------------------------
# PARSE OVERALL LATENCY LOG
# -----------------------------
overall_p95 = None

for line in agent_log.splitlines():
    if line.strip().startswith("read") and "avg" not in line:
        nums = re.findall(r"[\d.]+", line)
        if len(nums) >= 13:
            overall_p95 = float(nums[11])

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
# PARSE CPU LOG (SMOOTHED 5s)
# -----------------------------
cpu_rows = []

for line in cpu_log.splitlines():
    try:
        ts_str, cpu_val = line.strip().split(",")
        t = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        cpu = float(cpu_val)
        cpu_rows.append([t, cpu])
    except:
        continue

cpu_df = pd.DataFrame(cpu_rows, columns=["time", "cpu"])

# smooth to 5-second intervals (mean)
cpu_df = (
    cpu_df
    .set_index("time")
    .resample("5s")
    .mean()
    .reset_index()
)

# -----------------------------
# BUILD SEGMENTS (IMPORTANT)
# -----------------------------
job_segments = []

for job, info in jobs.items():
    events = sorted(info["events"], key=lambda x: x["time"])

    for i in range(len(events)):
        start = events[i]["time"]

        if i < len(events) - 1:
            end = events[i + 1]["time"]
        else:
            end = info["end"] if info["end"] else start + timedelta(seconds=5)

        job_segments.append({
            "job": job,
            "start": start,
            "end": end,
            "cores": events[i]["cores"]
        })

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
gs = GridSpec(2, 2, width_ratios=[4, 1], height_ratios=[1, 1])

ax1 = fig.add_subplot(gs[0, 0])
ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
ax_text = fig.add_subplot(gs[:, 1])

ax1.set_xlim(xmin, xmax)
ax2.set_xlim(xmin, xmax)

ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

# -----------------------------
# TOP GRAPH
# -----------------------------
ax1.set_title("Latency and Throughput Timeline")

xs, qps_vals, p95_vals = [], [], []

# color palette (define once)
COLOR_QPS = "#16B416"   # teal
COLOR_P95 = "#DD0E0E"   # purple

for i in range(len(df)):
    t_start = df.iloc[i]["time"]
    t_end = df.iloc[i + 1]["time"] if i < len(df)-1 else t_start + timedelta(seconds=interval_seconds)

    xs.extend([t_start, t_end])
    qps_vals.extend([df.iloc[i]["qps"]] * 2)
    p95_vals.extend([df.iloc[i]["p95"]] * 2)

ax1.step(xs, qps_vals, where="post", linestyle="--", linewidth=2, label="QPS", color=COLOR_QPS)
ax1.set_ylabel("QPS", color=COLOR_QPS)
ax1.tick_params(axis="y", labelcolor=COLOR_QPS)

ax1b = ax1.twinx()
ax1b.step(xs, p95_vals, where="post", label="p95 latency", color=COLOR_P95)
ax1b.set_ylabel("p95 latency (ms)", color=COLOR_P95)

# baseline
ax1b.axhline(800, linestyle="--", alpha=0.5, color=COLOR_P95)

ax1b.set_ylabel("p95 latency (ms)", color=COLOR_P95)
ax1b.tick_params(axis="y", labelcolor=COLOR_P95)

ax1b.set_ylabel("p95 latency (ms)")

# CPU line (third axis)
COLOR_CPU = "#1f77b4"  # blue

ax1c = ax1.twinx()
ax1c.spines["right"].set_position(("outward", 60))  # offset so it doesn't overlap

ax1c.plot(cpu_df["time"], cpu_df["cpu"], color=COLOR_CPU, linewidth=1.5, label="CPU %")

ax1c.set_ylabel("CPU (%)", color=COLOR_CPU)
ax1c.tick_params(axis="y", labelcolor=COLOR_CPU)

ax1.grid(True, linestyle=":", alpha=0.5)

lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax1b.get_legend_handles_labels()
lines3, labels3 = ax1c.get_legend_handles_labels()

ax1.legend(lines + lines2 + lines3, labels + labels2 + labels3)

# -----------------------------
# GANTT (FIXED)
# -----------------------------
job_names = list(set(seg["job"] for seg in job_segments))
cmap = mpl.colormaps["tab10"]
job_color = {job: cmap(i % 10) for i, job in enumerate(job_names)}

for seg in job_segments:
    left = mdates.date2num(seg["start"])
    width = (seg["end"] - seg["start"]).total_seconds() / 86400

    for core in seg["cores"]:
        ax2.barh(
            core,
            width,
            left=left,
            height=0.4,
            color=job_color[seg["job"]],
            edgecolor="black"
        )

        ax2.text(
            left + width/2,
            core,
            seg["job"],
            ha="center",
            va="center",
            fontsize=8
        )

# -----------------------------
# SUMMARY PANEL
# -----------------------------
ax_text.axis("off")

lines = []
if overall_p95:
    lines.append(f"Overall p95\n{overall_p95:.1f} ms")

summary_text = "\n\n".join(lines)

ax_text.text(
    0.5, 0.5,
    summary_text,
    ha="center",
    va="center",
    fontsize=16,
    fontweight="bold"
)

plt.tight_layout()

output_file = f"{POLICY_DIR}/analysis_plot.png"
plt.savefig(output_file, dpi=300, bbox_inches="tight")

print(f"Plot saved to {output_file}")
plt.show()