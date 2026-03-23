import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ── Timing data (wall-clock "real" time in seconds) ──────────────────────────
# Format: {job: [T1, T2, T4, T8]}
times = {
    "barnes":       [134.626,  68.417,  36.947,  28.349],
    "blackscholes": [127.547,  75.153,  48.149,  37.786],
    "canneal":      [251.396, 154.612, 101.349,  80.520],
    "freqmine":     [505.428, 256.725, 131.229, 105.243],
    "radix":         [ 53.703,  27.324,  13.909,   8.852],
    "streamcluster": [580.629, 296.761, 155.936,  99.394],
    "vips":          [103.018,  52.224,  28.117,  23.496],
}

threads = [1, 2, 4, 8]

# ── Compute speedups (T1 / Tn) ───────────────────────────────────────────────
speedups = {job: [ts[0] / t for t in ts] for job, ts in times.items()}

# ── Plot styling ─────────────────────────────────────────────────────────────
colors  = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf"]
markers = ["o", "s", "^", "D", "v", "P", "X"]

fig, ax = plt.subplots(figsize=(8, 5))

for (job, spd), color, marker in zip(speedups.items(), colors, markers):
    ax.plot(threads, spd, label=job, color=color,
            marker=marker, markersize=7, linewidth=2)

# Ideal (linear) speedup reference
ax.plot(threads, threads, linestyle="--", color="black",
        linewidth=1.2, label="ideal (linear)", zorder=0)

# ── Axes & labels ────────────────────────────────────────────────────────────
ax.set_xlabel("Number of Threads", fontsize=12)
ax.set_ylabel("Speedup  (T₁ / Tₙ)", fontsize=12)
ax.set_title("PARSEC Benchmark Speedup vs. Number of Threads", fontsize=13, fontweight="bold")

ax.set_xticks(threads)
ax.set_xlim(0.8, 8.5)
ax.set_ylim(0.8, 7)
ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
ax.xaxis.set_minor_locator(ticker.AutoMinorLocator(2))

ax.grid(which="major", linestyle="--", linewidth=0.6, alpha=0.6)
ax.grid(which="minor", linestyle=":",  linewidth=0.4, alpha=0.4)

ax.legend(loc="upper left", fontsize=10, framealpha=0.9)

saveLoc = "./part2b/Plots/parsec_speedup"

plt.tight_layout()
plt.savefig(f"{saveLoc}.pdf", dpi=150)
plt.savefig(f"{saveLoc}.png", dpi=150)
plt.show()
print(f"Saved: {saveLoc}.pdf  /  {saveLoc}.png")

# ── Print speedup table for reference ────────────────────────────────────────
print(f"\n{'Job':<15} {'S(1)':>6} {'S(2)':>6} {'S(4)':>6} {'S(8)':>6}")
print("-" * 40)
for job, spd in speedups.items():
    print(f"{job:<15} {spd[0]:>6.2f} {spd[1]:>6.2f} {spd[2]:>6.2f} {spd[3]:>6.2f}")