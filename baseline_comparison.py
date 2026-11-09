
from pathlib import Path
from collections import defaultdict
import pandas as pd

# Folder containing all 10 experimental results
metrics_dir = Path("runs/janus_baseline_comparison/metrics")

# Collect all CSV files
csv_files = sorted(metrics_dir.glob("run_*.csv"))

if not csv_files:
    raise FileNotFoundError("No experiment CSV files found.")

# Read and combine results
df = pd.concat(
    [pd.read_csv(file) for file in csv_files],
    ignore_index=True
)

# Metrics we want to compare
metrics = [
    "desired_tool_calls",
    "out_of_alignment_tool_calls",
    "attack_tool_calls",
    "permission_assistant_messages",
    "output_passes",
    "output_fails"
]

# Calculate averages for each baseline
summary = df.groupby("permission_assistant")[metrics].mean()

# Count runs
summary.insert(
    0, "number_of_runs",
    df.groupby("permission_assistant").size()
)

print("\n===== JANUS BASELINE COMPARISON =====\n")
print(summary.to_string())

# Save results
output_file = Path(
    "runs/janus_baseline_comparison/baseline_summary.csv"
)

summary.to_csv(output_file)

print(f"\nComparison saved to: {output_file}")
