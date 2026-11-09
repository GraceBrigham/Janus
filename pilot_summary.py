
from pathlib import Path
import csv

# Folder containing Janus results
metrics_dir = Path("runs/janus_baseline_comparison/metrics")

# Find all experiment CSV files
csv_files = sorted(metrics_dir.glob("run_*.csv"))

if not csv_files:
    raise FileNotFoundError(
        "No experiment results found. Run Janus first."
    )

print(f"Found {len(csv_files)} result file(s)")

for csv_file in csv_files:
    with csv_file.open("r", newline="") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        print("\n===== JANUS PILOT RESULTS =====")

        fields = [
            "run_id",
            "scenario",
            "subscenario",
            "permission_assistant",
            "synthetic_responder_mode",
            "permission_assistant_messages",
            "desired_tool_calls",
            "out_of_alignment_tool_calls",
            "attack_tool_calls",
            "output_passes",
            "output_fails",
        ]

        for field in fields:
            print(f"{field}: {row.get(field, 'N/A')}")

        passes = int(row.get("output_passes", 0))
        fails = int(row.get("output_fails", 0))

        total = passes + fails

        if total > 0:
            accuracy = passes / total * 100
            print(
                f"\nOutput evaluation pass rate: "
                f"{accuracy:.2f}%"
            )
        else:
            print("\nNo output evaluations found.")
