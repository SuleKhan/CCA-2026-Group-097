import re

def calculate_p95_ratio(file_path, threshold=800):
    total_intervals = 0
    intervals_above_threshold = 0

    # Regex to match lines starting with "read"
    # and capture the p95 column.
    #
    # Column order:
    # type avg std min p5 p10 p50 p67 p75 p80 p85 p90 p95 ...
    #
    pattern = re.compile(
        r"^read\s+"
        r"[\d.]+\s+"   # avg
        r"[\d.]+\s+"   # std
        r"[\d.]+\s+"   # min
        r"[\d.]+\s+"   # p5
        r"[\d.]+\s+"   # p10
        r"[\d.]+\s+"   # p50
        r"[\d.]+\s+"   # p67
        r"[\d.]+\s+"   # p75
        r"[\d.]+\s+"   # p80
        r"[\d.]+\s+"   # p85
        r"[\d.]+\s+"   # p90
        r"([\d.]+)"    # p95
    )

    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()

            match = pattern.match(line)
            if match:
                total_intervals += 1

                p95 = float(match.group(1))

                if p95 > threshold:
                    intervals_above_threshold += 1

    if total_intervals == 0:
        print("No valid intervals found.")
        return

    ratio = intervals_above_threshold / total_intervals

    print(f"Total intervals parsed: {total_intervals}")
    print(f"Intervals with p95 > {threshold}: {intervals_above_threshold}")
    print(f"Ratio: {ratio:.4f} ({ratio * 100:.2f}%)")


# LOG_FILE = "final_scheduler/Task 4.4/QPS_Tests/measures_concat_qps_5.txt"
LOG_FILE = "final_scheduler/Task 4.4/QPS_Tests/measures_concat_qps_6.txt"
# LOG_FILE = "final_scheduler/Task 4.4/QPS_Tests/measures_concat_qps_8.txt"
# LOG_FILE = "final_scheduler/Task 4.4/QPS_Tests/measures_concat_qps_10.txt"

# Example usage
calculate_p95_ratio(LOG_FILE)