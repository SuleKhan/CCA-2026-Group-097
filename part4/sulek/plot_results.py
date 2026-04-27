import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.dates import DateFormatter
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import re

# Parse jobs log
def parse_jobs_log(filename):
    jobs = []
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            timestamp = datetime.fromisoformat(parts[0])
            event = parts[1]
            job_name = parts[2]
            
            if event == 'start':
                jobs.append({
                    'name': job_name,
                    'start': timestamp,
                    'end': None,
                    'cores': parts[3] if len(parts) > 3 else None,
                    'threads': parts[4] if len(parts) > 4 else None
                })
            elif event == 'end':
                for job in reversed(jobs):
                    if job['name'] == job_name and job['end'] is None:
                        job['end'] = timestamp
                        break
    
    return jobs

# Parse measure log
def parse_measure_log(filename):
    measurements = []
    with open(filename, 'r') as f:
        lines = f.readlines()
        
    # Find where data starts
    start_time = None
    end_time = None
    interval_count = 0
    
    for i, line in enumerate(lines):
        if 'Timestamp start:' in line:
            start_time = int(line.split(':')[1].strip()) / 1000.0  # Convert to seconds
        elif 'Timestamp end:' in line:
            end_time = int(line.split(':')[1].strip()) / 1000.0
        elif 'Total number of intervals' in line:
            # Extract interval count and QPS targets
            match = re.search(r'Total number of intervals = (\d+) \((.*?)\)', line)
            if match:
                interval_count = int(match.group(1))
                qps_targets = list(map(int, match.group(2).split(', ')))
        elif line.startswith('read'):
            # Parse latency data
            parts = line.split()
            if len(parts) > 15:
                try:
                    p95 = float(parts[11])  # p95 column
                    p99 = float(parts[12])  # p99 column
                    actual_qps = float(parts[15])  # QPS column
                    target_qps = float(parts[16])  # target column
                    
                    measurements.append({
                        'p95': p95,
                        'p99': p99,
                        'avg': float(parts[1]),
                        'actual_qps': actual_qps,
                        'target_qps': target_qps
                    })
                except (ValueError, IndexError):
                    pass
    
    return measurements, start_time, end_time, interval_count

# Parse agent log
def parse_agent_log(filename):
    agent_data = {}
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if 'Local QPS' in line:
                parts = re.findall(r'[\d.]+', line)
                if len(parts) >= 3:
                    agent_data['local_qps'] = float(parts[0])
            elif 'Total QPS' in line:
                parts = re.findall(r'[\d.]+', line)
                if len(parts) >= 1:
                    agent_data['total_qps'] = float(parts[0])
            elif 'CPU Usage' in line:
                parts = re.findall(r'[\d.]+', line)
                if len(parts) >= 3:
                    agent_data['cpu_avg'] = float(parts[0])
                    agent_data['cpu_min'] = float(parts[1])
                    agent_data['cpu_max'] = float(parts[2])
    
    return agent_data

# Helper function to find which job was running at a given time
def get_job_at_time(jobs, timestamp):
    for job in jobs:
        if job['name'] not in ['scheduler', 'update_cores', 'memcached']:
            if job['start'] <= timestamp <= (job['end'] if job['end'] else timestamp):
                return job['name']
    return None

# Main plotting function
def create_comprehensive_plot(jobs_log, measure_log, agent_log):
    jobs = parse_jobs_log(jobs_log)
    measurements, start_time_ms, end_time_ms, interval_count = parse_measure_log(measure_log)
    agent_data = parse_agent_log(agent_log)
    
    # Get base timestamp from jobs (scheduler start)
    base_time = None
    for job in jobs:
        if job['name'] == 'scheduler':
            base_time = job['start']
            break
    
    if base_time is None:
        base_time = jobs[0]['start']
    
    # Convert measure log timestamps to seconds from base_time
    base_timestamp = base_time.timestamp()
    start_time = start_time_ms  # Already in seconds from parse_measure_log
    end_time = end_time_ms
    
    # Calculate time offset between jobs log and measure log
    # Measure log timestamps are in seconds, jobs log are datetimes
    interval_duration = 15  # seconds per interval (from the test)
    
    # Create synchronized time points
    time_points = []
    measurement_times = []
    for i in range(len(measurements)):
        measurement_time_s = start_time + i * interval_duration
        measurement_times.append(measurement_time_s)
        # Convert to offset from base_time
        time_offset = measurement_time_s - base_timestamp
        time_points.append(time_offset)
    
    # Create figure with subplots
    fig = plt.figure(figsize=(18, 14))
    
    # 1. Gantt Chart of job execution with measurement intervals
    ax1 = plt.subplot(4, 1, 1)
    
    job_colors = {
        'memcached': 'red',
        'blackscholes': 'blue',
        'canneal': 'green',
        'freqmine': 'orange',
        'radix': 'purple',
        'streamcluster': 'brown',
        'barnes': 'pink',
        'vips': 'gray'
    }
    
    # Plot jobs as horizontal bars
    y_pos = 1
    for job in jobs:
        if job['name'] not in ['scheduler', 'update_cores'] and job['end'] is not None:
            start_offset = (job['start'] - base_time).total_seconds()
            duration = (job['end'] - job['start']).total_seconds()
            
            color = job_colors.get(job['name'], 'cyan')
            ax1.barh(y_pos, duration, left=start_offset, height=0.6, 
                    color=color, edgecolor='black', linewidth=1.5, alpha=0.7)
            
            ax1.text(start_offset + duration/2, y_pos, job['name'], 
                    ha='center', va='center', fontweight='bold', fontsize=9)
            y_pos += 1
    
    # Add measurement intervals as shaded backgrounds
    for i, (t_start, t_end) in enumerate(zip(time_points[:-1], time_points[1:])):
        color = 'lightblue' if i % 2 == 0 else 'lightyellow'
        ax1.axvspan(t_start, t_end, alpha=0.1, color=color)
    
    ax1.set_ylim(0.5, y_pos)
    ax1.set_xlabel('Time (seconds)', fontsize=11, fontweight='bold')
    ax1.set_title('Job Execution Timeline with Measurement Intervals', fontsize=13, fontweight='bold')
    ax1.grid(axis='x', alpha=0.3)
    ax1.set_yticks([])
    
    # Find the last completed job
    max_time = 0
    for job in jobs:
        if job['end'] is not None:
            max_time = max(max_time, (job['end'] - base_time).total_seconds())
    ax1.set_xlim(0, max(max_time, max(time_points) if time_points else 100))
    
    # 2. QPS over time
    ax2 = plt.subplot(4, 1, 2)
    
    if measurements and start_time and end_time:
        target_qps = [m['target_qps'] for m in measurements]
        actual_qps = [m['actual_qps'] for m in measurements]
        
        ax2.plot(time_points, target_qps, 'r--', linewidth=2, label='Target QPS', marker='o', markersize=4)
        ax2.plot(time_points, actual_qps, 'b-', linewidth=2, label='Actual QPS', marker='s', markersize=4)
        ax2.fill_between(time_points, actual_qps, alpha=0.2, color='blue')
        
        ax2.set_xlabel('Time (seconds)', fontsize=11, fontweight='bold')
        ax2.set_ylabel('QPS', fontsize=11, fontweight='bold')
        ax2.set_title('Memcached QPS Over Time (with Measurement Intervals)', fontsize=13, fontweight='bold')
        ax2.legend(loc='upper left', fontsize=10)
        ax2.grid(alpha=0.3)
        ax2.set_xlim(0, max(max_time, max(time_points) if time_points else 100))
    
    # 3. P95 Latency over time with SLO line
    ax3 = plt.subplot(4, 1, 3)
    
    if measurements:
        p95_latencies = [m['p95'] for m in measurements]
        p99_latencies = [m['p99'] for m in measurements]
        
        ax3.plot(time_points, p95_latencies, 'b-', linewidth=2.5, label='P95 Latency', marker='o', markersize=5)
        ax3.plot(time_points, p99_latencies, 'g--', linewidth=2, label='P99 Latency', marker='s', markersize=4)
        ax3.fill_between(time_points, p95_latencies, alpha=0.2, color='blue')
        
        # Add SLO line (0.8ms = 800 microseconds for Part 4)
        slo_p95 = 800  # microseconds
        ax3.axhline(slo_p95, color='red', linestyle='--', linewidth=2.5, label='SLO (0.8ms)')
        
        ax3.set_xlabel('Time (seconds)', fontsize=11, fontweight='bold')
        ax3.set_ylabel('Latency (μs)', fontsize=11, fontweight='bold')
        ax3.set_title('Memcached P95/P99 Latency Over Time', fontsize=13, fontweight='bold')
        ax3.legend(loc='upper right', fontsize=10)
        ax3.grid(alpha=0.3)
        ax3.set_xlim(0, max(max_time, max(time_points) if time_points else 100))
        
        # Check SLO violations
        violations = sum(1 for lat in p95_latencies if lat > slo_p95)
        ax3.text(0.02, 0.98, f'SLO Violations: {violations}/{len(p95_latencies)}', 
                transform=ax3.transAxes, fontsize=11, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
    
    # 4. Detailed table showing job at each interval + QPS + P95
    ax4 = plt.subplot(4, 1, 4)
    ax4.axis('off')
    
    # Create table data
    table_data = []
    table_data.append(['Interval', 'Time (s)', 'Job Running', 'Target QPS', 'Actual QPS', 'P95 Latency (μs)'])
    
    for i, (t_point, t_meas) in enumerate(zip(time_points[:min(20, len(time_points))], measurement_times[:20])):
        # Find which job was running at this time
        job_time = base_time + timedelta(seconds=t_point)
        running_job = get_job_at_time(jobs, job_time)
        
        if i < len(measurements):
            m = measurements[i]
            table_data.append([
                f'{i+1}',
                f'{t_point:.1f}',
                running_job if running_job else '-',
                f'{m["target_qps"]:.0f}',
                f'{m["actual_qps"]:.0f}',
                f'{m["p95"]:.1f}'
            ])
    
    # Create table
    table = ax4.table(cellText=table_data, cellLoc='center', loc='center',
                     colWidths=[0.08, 0.1, 0.15, 0.15, 0.15, 0.2])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 2)
    
    # Style header row
    for i in range(len(table_data[0])):
        table[(0, i)].set_facecolor('#40466e')
        table[(0, i)].set_text_props(weight='bold', color='white')
    
    # Alternate row colors
    for i in range(1, len(table_data)):
        for j in range(len(table_data[0])):
            if i % 2 == 0:
                table[(i, j)].set_facecolor('#f0f0f0')
            else:
                table[(i, j)].set_facecolor('white')
    
    ax4.set_title('Measurement Intervals Detail (first 20 shown)', fontsize=12, fontweight='bold', pad=20)
    
    plt.tight_layout()
    plt.savefig('scheduling_analysis.png', dpi=300, bbox_inches='tight')
    print(f"\n✓ Plot saved as 'scheduling_analysis.png'")
    
    # Print summary statistics
    print("\n" + "="*70)
    print("SCHEDULING ANALYSIS SUMMARY")
    print("="*70)
    
    print("\nJob Execution Times:")
    total_makespan = 0
    for job in jobs:
        if job['name'] not in ['scheduler', 'update_cores', 'memcached'] and job['end'] is not None:
            duration = (job['end'] - job['start']).total_seconds()
            print(f"  {job['name']:15} : {duration:7.2f}s")
            total_makespan = max(total_makespan, (job['end'] - base_time).total_seconds())
    
    print(f"\nTotal Makespan (from first job to last): {total_makespan:.2f}s")
    
    if measurements:
        p95_latencies = [m['p95'] for m in measurements]
        p99_latencies = [m['p99'] for m in measurements]
        target_qps = [m['target_qps'] for m in measurements]
        actual_qps = [m['actual_qps'] for m in measurements]
        
        print(f"\nMemcached Performance:")
        print(f"  Avg QPS (actual):    {np.mean(actual_qps):.2f}")
        print(f"  Avg QPS (target):    {np.mean(target_qps):.2f}")
        print(f"  Avg P95 Latency:     {np.mean(p95_latencies):.2f} μs")
        print(f"  Max P95 Latency:     {np.max(p95_latencies):.2f} μs")
        print(f"  Min P95 Latency:     {np.min(p95_latencies):.2f} μs")
        print(f"  Avg P99 Latency:     {np.mean(p99_latencies):.2f} μs")
        
        violations = sum(1 for lat in p95_latencies if lat > 800)
        print(f"  SLO Violations:      {violations}/{len(p95_latencies)}")
        print(f"  SLO Compliance:      {(1 - violations/len(p95_latencies))*100:.1f}%")
    
    if agent_data:
        print(f"\nAgent Statistics:")
        if 'total_qps' in agent_data:
            print(f"  Total QPS:           {agent_data['total_qps']:.2f}")
        if 'cpu_avg' in agent_data:
            print(f"  CPU Usage (avg/max): {agent_data['cpu_avg']:.2f}% / {agent_data['cpu_max']:.2f}%")
    
    print("="*70 + "\n")


if __name__ == "__main__":
    # Path to the log files
    jobs_log_path = "single_job_seq/test_jobs_log.txt"
    measure_log_path = "single_job_seq/test_measure_log.txt"
    agent_log_path = "single_job_seq/test_agent_log.txt"
    
    try:
        create_comprehensive_plot(jobs_log_path, measure_log_path, agent_log_path)
        plt.show()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
