# LOG_INTERVAL_FILE = "single_job_seq/test_measure_log.txt"
# LOG_SCHED_FILE = "single_job_seq/test_jobs_log.txt"
import re
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec

# -----------------------------
# FILE INPUTS
# -----------------------------
# POLICY_DIR = "single_job_seq"
POLICY_DIR = "single_job_2_core_seq"

LOG_AGENT_FILE = f"{POLICY_DIR}/agent_log.txt" 
LOG_INTERVAL_FILE = f"{POLICY_DIR}/measure_log.txt"
LOG_SCHED_FILE = f"{POLICY_DIR}/jobs_log.txt" 

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
# PARSE OVERALL LATENCY LOG
# -----------------------------
overall_p95 = None

for line in agent_log.splitlines():
    if line.strip().startswith("read") and "avg" not in line:
        nums = re.findall(r"[\d.]+", line)
        if len(nums) >= 13:
            overall_p95 = float(nums[11])  # p95 column

# -----------------------------
# PARSE INTERVAL LOG
# -----------------------------
start_ts_match = re.search(r"Timestamp\s+start:\s*(\d+)", measure_log)
if not start_ts_match:
    raise ValueError("Missing Timestamp start in interval log")

start_ts = None
end_ts = None

for line in measure_log.splitlines():
    if "Timestamp start" in line:
        start_ts = int(line.split(":")[1].strip())

    if "Timestamp end" in line:
        end_ts = int(line.split(":")[1].strip())

if start_ts is None or end_ts is None:
    raise ValueError("Missing start or end timestamp in interval log")

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
# PARSE SCHEDULER LOG
# -----------------------------
job_start_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+start\s+(\w+)\s+\[([^\]]+)\]\s+(\d+)"
)

job_end_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+end\s+(\w+)"
)

jobs = {}

for line in jobs_log.splitlines():
    # START LINE
    m = job_start_re.search(line)
    if m:
        t, job, cores, threads = m.groups()

        cores = cores.strip("[]").split(",")
        cores = [int(c.strip()) for c in cores]
        print(cores)
        jobs[job] = {
            "start": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
            "cores": cores
        }
        continue

    # END LINE (NO CORE HERE)
    m = job_end_re.search(line)
    if m:
        t, job = m.groups()
        if job in jobs:
            jobs[job]["end"] = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)

jobs["memcached"] = {
    "start": datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc),
    "end": datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc),
    "cores": [0]
}

# -----------------------------
# TOTAL JOB RUNTIME
# -----------------------------
target_jobs = [
    "barnes", "blackscholes", "canneal",
    "freqmine", "radix", "streamcluster", "vips"
]

job_times = []

for name in target_jobs:
    if name in jobs and "end" in jobs[name]:
        job_times.append(jobs[name]["start"])
        job_times.append(jobs[name]["end"])

if job_times:
    total_start = min(job_times)
    total_end = max(job_times)
    total_runtime = (total_end - total_start).total_seconds()
else:
    total_runtime = None

# -----------------------------
# PLOTTING
# -----------------------------
all_times = []

# interval log times
all_times.extend(df["time"].tolist())

# scheduler times
for job in jobs.values():
    all_times.append(job["start"])
    if "end" in job:
        all_times.append(job["end"])

xmin = min(all_times)
xmax = max(all_times)

fig = plt.figure(figsize=(18, 10))
gs = GridSpec(2, 2, width_ratios=[4, 1], height_ratios=[1, 1])

ax1 = fig.add_subplot(gs[0, 0])
ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
ax_text = fig.add_subplot(gs[:, 1])  # right-side panel (spans both rows)
ax1.set_xlim(xmin, xmax)
ax2.set_xlim(xmin, xmax)

ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
# -----------------------------
# TOP: STEP-BASED TIMELINE (IMPROVED)
# -----------------------------
ax1.set_title("Latency and Throughput Timeline")

# build step-style data (like their approach)
xs = []
qps_vals = []
p95_vals = []

for i in range(len(df)):
    t_start = df.iloc[i]["time"]
    if i < len(df) - 1:
        t_end = df.iloc[i + 1]["time"]
    else:
        t_end = t_start + timedelta(seconds=interval_seconds)

    xs.extend([t_start, t_end])
    qps_vals.extend([df.iloc[i]["qps"]] * 2)
    p95_vals.extend([df.iloc[i]["p95"]] * 2)

# color palette (define once)
COLOR_QPS = "#16B416"   # teal
COLOR_P95 = "#DD0E0E"   # purple

# QPS (left axis)
ax1.step(
    xs,
    qps_vals,
    where="post",
    label="QPS",
    linestyle="--",
    linewidth=2,
    color=COLOR_QPS
)
ax1.set_ylabel("QPS", color=COLOR_QPS)
ax1.tick_params(axis="y", labelcolor=COLOR_QPS)

# P95 (right axis)
ax1b = ax1.twinx()
ax1b.step(
    xs,
    p95_vals,
    where="post",
    label="p95 latency",
    color=COLOR_P95
)

# baseline
ax1b.axhline(800, linestyle="--", alpha=0.5, color=COLOR_P95)

ax1b.set_ylabel("p95 latency (ms)", color=COLOR_P95)
ax1b.tick_params(axis="y", labelcolor=COLOR_P95)

ax1b.set_ylabel("p95 latency (ms)")

# cleaner grid (closer to theirs, but softer)
ax1.grid(True, linestyle=":", alpha=0.5)

# legend merge
lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax1b.get_legend_handles_labels()
ax1.legend(lines + lines2, labels + labels2, loc="upper left")

# -----------------------------
# BOTTOM: MULTI-CORE GANTT
# -----------------------------
for job, info in jobs.items():
    start = info["start"]
    end = info.get("end", start + timedelta(seconds=10))
    cores = info["cores"]

    left = mdates.date2num(start)
    width = (end - start).total_seconds() / 86400

    for core in cores:
        ax2.barh(
            core,
            width,
            left=left,
            height=0.4,
            alpha=0.85
        )

    # label once per job (not per core → avoids clutter)
    mid = start + (end - start) / 2

    ax2.text(
        mid,
        sum(cores) / len(cores),  # center across cores
        job,
        va="center",
        ha="center",
        fontsize=8,
        bbox=dict(facecolor="white", alpha=0.5, edgecolor="none")
    )

# -----------------------------
# RIGHT-SIDE SUMMARY PANEL
# -----------------------------
ax_text.axis("off")  # no axes

lines = []

if overall_p95 is not None:
    lines.append(f"Overall p95\n{overall_p95:.1f} ms")

if total_runtime is not None:
    lines.append(f"Total Runtime\n{total_runtime:.1f} s")

summary_text = "\n\n".join(lines)

ax_text.text(
    0.5, 0.5,
    summary_text,
    ha="center",
    va="center",
    fontsize=16,
    fontweight="bold",
    bbox=dict(
        boxstyle="round,pad=1.0",
        facecolor="white",
        edgecolor="black",
        linewidth=2
    )
)

plt.tight_layout()

# SAVE FIGURE
output_file = f"{POLICY_DIR}/analysis_plot.png"
plt.savefig(output_file, dpi=300, bbox_inches="tight")

print(f"Plot saved to {output_file}")

plt.show()