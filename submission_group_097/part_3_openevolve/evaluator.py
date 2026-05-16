import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openevolve.evaluation_result import EvaluationResult

root = Path(__file__).resolve().parents[1]
out = root / "part3_2104_results"
jobs = {"parsec-barnes", "parsec-blackscholes", "parsec-canneal", "parsec-freqmine", "parsec-radix", "parsec-streamcluster", "parsec-vips"}


def res(score, make=9999, p95=9999, ratio=1):
    return EvaluationResult(metrics={
        "combined_score": float(score),
        "makespan_s": float(make),
        "max_p95_us": float(p95),
        "slo_ratio": float(ratio),
    })


def run(cmd, timeout=None):
    return subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=timeout)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def pod_times(path):
    data = json.loads(path.read_text())
    got = {}

    for pod in data["items"]:
        lab = pod["metadata"].get("labels", {})
        name = lab.get("job-name") or lab.get("batch.kubernetes.io/job-name")
        if name not in jobs:
            continue

        st = pod.get("status", {}).get("containerStatuses", [])
        if not st:
            continue

        term = st[0].get("state", {}).get("terminated")
        if not term or term.get("exitCode") != 0:
            continue

        a, b = ts(term["startedAt"]), ts(term["finishedAt"])
        if name not in got or a > got[name][0]:
            got[name] = (a, b)

    if set(got) != jobs:
        raise RuntimeError("missing jobs")

    start = min(a for a, b in got.values())
    end = max(b for a, b in got.values())
    return end - start, start, end


def mcperf(path, start, end):
    head = None
    vals = []

    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("#type"):
            head = [x.lstrip("#") for x in line.split()]
        elif head and line.startswith("read"):
            row = dict(zip(head, line.split()))
            a = float(row.get("ts_start", start * 1000)) / 1000
            b = float(row.get("ts_end", end * 1000)) / 1000
            if b >= start and a <= end:
                vals.append(float(row["p95"]))

    if not vals:
        raise RuntimeError("no mcperf data")

    bad = sum(x > 1000 for x in vals)
    return max(vals), bad / len(vals)


def evaluate(program_path: str) -> EvaluationResult:
    name = f"oe_{int(time.time())}_{os.getpid()}"

    try:
        c = run([sys.executable, "-m", "py_compile", program_path], 60)
        if c.returncode:
            return res(-1000000)

        p = run([sys.executable, program_path, "--run-id", name], int(os.environ.get("cca_timeout", "900")))
        if p.returncode:
            return res(-1000000)

        make, start, end = pod_times(out / name / "pods.json")
        p95, ratio = mcperf(out / name / "mcperf.txt", start, end)
        score = -make - 1500 * ratio - max(0, p95 - 1000)

        return res(score, make, p95, ratio)

    except Exception:
        return res(-1000000)
