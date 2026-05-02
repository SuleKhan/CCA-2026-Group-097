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
POLICY_DIR = "single_job_seq"

LOG_AGENT_FILE = f"{POLICY_DIR}/agent_log.txt" 
LOG_INTERVAL_FILE = f"{POLICY_DIR}/measure_log.txt"
LOG_SCHED_FILE = f"{POLICY_DIR}/jobs_log.txt" 

# -----------------------------
# LOAD FILES
# -----------------------------
with open(LOG_AGENT_FILE, "r", encoding="utf-8", errors="ignore") as f:
    log1 = f.read()

with open(LOG_INTERVAL_FILE, "r", encoding="utf-8", errors="ignore") as f:
    log2 = f.read()

with open(LOG_SCHED_FILE, "r", encoding="utf-8", errors="ignore") as f:
    log3 = f.read()


# -----------------------------
# PARSE OVERALL LATENCY LOG
# -----------------------------
overall_p95 = None

for line in log1.splitlines():
    if line.strip().startswith("read") and "avg" not in line:
        nums = re.findall(r"[\d.]+", line)
        if len(nums) >= 13:
            overall_p95 = float(nums[11])  # p95 column

# -----------------------------
# PARSE INTERVAL LOG
# -----------------------------
start_ts_match = re.search(r"Timestamp\s+start:\s*(\d+)", log2)
if not start_ts_match:
    raise ValueError("Missing Timestamp start in interval log")

start_ts = None
end_ts = None

for line in log2.splitlines():
    if "Timestamp start" in line:
        start_ts = int(line.split(":")[1].strip())

    if "Timestamp end" in line:
        end_ts = int(line.split(":")[1].strip())

if start_ts is None or end_ts is None:
    raise ValueError("Missing start or end timestamp in interval log")

interval_seconds = 15

rows = []
interval_idx = 0

for line in log2.splitlines():
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
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+start\s+(\w+)\s+\[(\d+)\]"
)

job_end_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+)\s+end\s+(\w+)"
)

jobs = {}

for line in log3.splitlines():

    # START LINE
    m = job_start_re.search(line)
    if m:
        t, job, core = m.groups()
        jobs[job] = {
            "start": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
            "core": int(core)
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
    "core": 0
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
# TOP: DUAL AXIS (QPS + p95)
# -----------------------------
ax1.set_title("QPS and p95 Latency Over Time")

ax1.plot(df["time"], df["qps"], label="QPS", color="blue")
ax1.set_ylabel("QPS", color="blue")

ax1b = ax1.twinx()
ax1b.plot(df["time"], df["p95"], label="p95 (ms)", color="red")
ax1b.axhline(800, linestyle="--", color="gray", label="baseline 0.8ms")
ax1b.set_ylabel("p95 latency (ms)", color="red")

ax1.set_xlabel("Time")

# merged legend
lines, labels = ax1.get_legend_handles_labels()
lines2, labels2 = ax1b.get_legend_handles_labels()
ax1.legend(lines + lines2, labels + labels2, loc="upper left")

# -----------------------------
# BOTTOM: GANTT CHART WITH LABELS
# -----------------------------
for job, info in jobs.items():
    start = info["start"]
    end = info.get("end", start + timedelta(seconds=10))
    core = info["core"]

    duration = (end - start).total_seconds()

    import matplotlib.dates as mdates

    left = mdates.date2num(start)
    width = (end - start).total_seconds() / 86400  # seconds → days

    ax2.barh(core, width, left=left, height=0.4)

    # TRUE midpoint (correct for datetime math)
    mid = start + timedelta(seconds=(end - start).total_seconds() / 2)

    ax2.text(
        mid,
        core,
        job,
        va="center",
        ha="center",
        fontsize=8,
        bbox=dict(facecolor="white", alpha=0.5, edgecolor="none")
    )

ax2.set_title("Gantt Chart (Jobs per Core)")
ax2.set_xlabel("Time")
ax2.set_ylabel("Core")

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