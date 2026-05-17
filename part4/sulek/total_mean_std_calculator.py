import pandas as pd

# Load CSV
df = pd.read_csv("execution_jobs.csv")

# Sum all job durations per run
run_totals = (
    df.groupby("run")["duration_s"]
    .sum()
    .reset_index(name="total_runtime_s")
)

# Compute statistics
mean_total = run_totals["total_runtime_s"].mean()
std_total = run_totals["total_runtime_s"].std(ddof=1)  # sample std

# Print results
print("Per-run total runtimes:")
print(run_totals)

print(f"\nMean total runtime: {mean_total:.6f} s")
print(f"Std total runtime:  {std_total:.6f} s")