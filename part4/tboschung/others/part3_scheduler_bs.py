#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

zone = "europe-west1-b"
ssh_key = os.path.expanduser("~/.ssh/cloud-computing")
mcperf_dir = "~/memcache-perf-dynamic"
out_dir = Path("part3_results")
get_time = Path("get_time.py")
default_memcached_yaml = Path("memcache-t1-part3.yaml")

streamcluster_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-streamcluster, labels: {app: parsec-streamcluster}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-streamcluster}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: streamcluster
        image: anakli/cca:parsec_streamcluster
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-6 ./run -a run -S parsec -p streamcluster -i native -n 7"]
        resources:
          requests: {cpu: "7", memory: "4Gi"}
          limits: {cpu: "7", memory: "4Gi"}
""".strip()

barnes_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-barnes, labels: {app: parsec-barnes}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-barnes}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: barnes
        image: anakli/cca:splash2x_barnes
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-6 ./run -a run -S splash2x -p barnes -i native -n 7"]
        resources:
          requests: {cpu: "7", memory: "4Gi"}
          limits: {cpu: "7", memory: "4Gi"}
""".strip()

freqmine_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-freqmine, labels: {app: parsec-freqmine}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-freqmine}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: freqmine
        image: anakli/cca:parsec_freqmine
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-6 ./run -a run -S parsec -p freqmine -i native -n 7"]
        resources:
          requests: {cpu: "7", memory: "4Gi"}
          limits: {cpu: "7", memory: "4Gi"}
""".strip()

radix_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-radix, labels: {app: parsec-radix}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-radix}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: radix
        image: anakli/cca:splash2x_radix
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-3 ./run -a run -S splash2x -p radix -i native -n 4"]
        resources:
          requests: {cpu: "4", memory: "8Gi"}
          limits: {cpu: "4", memory: "8Gi"}
""".strip()

blackscholes_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-blackscholes, labels: {app: parsec-blackscholes}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-blackscholes}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-a-8core}
      containers:
      - name: blackscholes
        image: anakli/cca:parsec_blackscholes
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 0-3 ./run -a run -S parsec -p blackscholes -i native -n 4"]
        resources:
          requests: {cpu: "4", memory: "2Gi"}
          limits: {cpu: "4", memory: "2Gi"}
""".strip()

canneal_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-canneal, labels: {app: parsec-canneal}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-canneal}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-b-4core}
      containers:
      - name: canneal
        image: anakli/cca:parsec_canneal
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 1-2 ./run -a run -S parsec -p canneal -i native -n 2"]
        resources:
          requests: {cpu: "2", memory: "2Gi"}
          limits: {cpu: "2", memory: "2Gi"}
""".strip()

vips_yaml = """
apiVersion: batch/v1
kind: Job
metadata: {name: parsec-vips, labels: {app: parsec-vips}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: parsec-vips}}
    spec:
      restartPolicy: Never
      nodeSelector: {cca-project-nodetype: node-b-4core}
      containers:
      - name: vips
        image: anakli/cca:parsec_vips
        imagePullPolicy: IfNotPresent
        command: ["sh", "-lc"]
        args: ["taskset -c 1-2 ./run -a run -S parsec -p vips -i native -n 2"]
        resources:
          requests: {cpu: "2", memory: "2Gi"}
          limits: {cpu: "2", memory: "2Gi"}
""".strip()

#POLICY

nodea = [
    {"job": "parsec-streamcluster", "threads": 7, "cores": "0-6", "manifest": streamcluster_yaml},
    {"job": "parsec-barnes", "threads": 7, "cores": "0-6", "manifest": barnes_yaml},
    {"job": "parsec-blackscholes", "threads": 4, "cores": "0-3", "manifest": blackscholes_yaml},
    {"job": "parsec-freqmine", "threads": 7, "cores": "0-6", "manifest": freqmine_yaml},
    {"job": "parsec-radix", "threads": 4, "cores": "0-3", "manifest": radix_yaml},
]

nodeb = [
    {"job": "parsec-canneal", "threads": 2, "cores": "1-2", "manifest": canneal_yaml},
    {"job": "parsec-vips", "threads": 2, "cores": "1-2", "manifest": vips_yaml},
]

#run command helper
def run(cmd, check=True, input=None):
    p = subprocess.run(cmd, text=True, input=input, capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"Command failed: {cmd}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return p

#ssh into VM node
def ssh(node, cmd, check=True):
    return run(["gcloud", "compute", "ssh", f"--ssh-key-file={ssh_key}", f"ubuntu@{node}", "--zone", zone, "--command", cmd], check=check)

#copy from remote to local
def scp_from(node, remote_path, local_path):
    run(["gcloud", "compute", "scp", f"--ssh-key-file={ssh_key}", f"ubuntu@{node}:{remote_path}", str(local_path), "--zone", zone])

#run job
def apply_manifest(text):
    out = run(["kubectl", "apply", "-f", "-"], input=text).stdout.strip()
    if out:
        print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    memcached_yaml = default_memcached_yaml

    out_dir.mkdir(exist_ok=True)
    run_dir = out_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    #get real node names for dict
    nodes = {
        "client-agent": run(["kubectl", "get", "nodes", "-l", "cca-project-nodetype=client-agent-a", "-o", "jsonpath={.items[0].metadata.name}"]).stdout.strip(),
        "client-measure": run(["kubectl", "get", "nodes", "-l", "cca-project-nodetype=client-measure", "-o", "jsonpath={.items[0].metadata.name}"]).stdout.strip(),
        "node-a-8core": run(["kubectl", "get", "nodes", "-l", "cca-project-nodetype=node-a-8core", "-o", "jsonpath={.items[0].metadata.name}"]).stdout.strip(),
        "node-b-4core": run(["kubectl", "get", "nodes", "-l", "cca-project-nodetype=node-b-4core", "-o", "jsonpath={.items[0].metadata.name}"]).stdout.strip(),
    }

    #find Ip addresses nodes
    agent_a_ip = run(["kubectl", "get", "node", nodes["client-agent-a"],"-o", "jsonpath={.status.addresses[?(@.type=='InternalIP')].address}"]).stdout.strip()
    agent_b_ip = run(["kubectl", "get", "node", nodes["client-agent-b"],"-o", "jsonpath={.status.addresses[?(@.type=='InternalIP')].address}"]).stdout.strip()


    print("\n step 1: Clean old resources and deploy memcached")
    run(["kubectl", "delete", "jobs", "--all", "--ignore-not-found=true"], check=False)
    run(["kubectl", "delete", "-f", str(memcached_yaml), "--ignore-not-found=true"], check=False)
    #start new memcached 
    run(["kubectl", "apply", "-f", str(memcached_yaml)])
    run(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "name=some-memcached", "--timeout=300s"])
    memcached_ip =run(["kubectl", "get","pods", "-l", "name=some-memcached", "-o", "jsonpath={.items[0].status.podIP}"]).stdout.strip()
    print(f"pod IP: {memcached_ip}")

    print("\n step 2: Start first two agents")
    for node, threads, log_name in [(nodes["client-agent-a"], 2, "/tmp/mcperf-agent-a.log"), (nodes["client-agent-b"], 4, "/tmp/mcperf-agent-b.log")]:
        #kill old mcperf processes and start new one in agent mode
        remote = textwrap.dedent(f"""
            cd {mcperf_dir}
            pkill -x mcperf || true
            rm -f {log_name}
            nohup bash -lc 'cd {mcperf_dir} && ./mcperf -T {threads} -A' > {log_name} 2>&1 < /dev/null &
            sleep 1
        """).strip()
        print(node, ssh(node, remote).stdout.strip())

    print("\n step 3: steady-load measurement")
    remote_mcperf_path = f"~/mcperf_{args.run_id}.txt"
    remote_pid_path = f"~/mcperf_{args.run_id}.pid"
    scan = "30000:30500:5"
    #fill memcached with key-value data before measurement (./mcperf) and start measurement (nohup)
    remote = textwrap.dedent(f"""
        cd {mcperf_dir}
        ./mcperf -s {memcached_ip} --loadonly > /tmp/mcperf-loadonly-{args.run_id}.log 2>&1
        nohup bash -lc 'cd {mcperf_dir} && stdbuf -oL ./mcperf -s {memcached_ip} -a {agent_a_ip} -a {agent_b_ip} --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 --scan {scan} > {remote_mcperf_path} 2>&1' >/dev/null 2>&1 &
        echo $! > {remote_pid_path}
        sleep 1
        test -f {remote_mcperf_path}
        echo {remote_mcperf_path}
    """).strip()
    print(f"measurement log on {nodes['client-measure']}: {ssh(nodes['client-measure'], remote).stdout.strip().splitlines()[-1]}")
    time.sleep(10)

    print("\nstep 4 Launch jobs on both worker nodes")
    queues = {"node-a-8core": list(nodea), "node-b-4core": list(nodeb)}
    running = {"node-a-8core": None, "node-b-4core": None}
    #Main policy execution loop!!
    while any(queues.values()) or any(running.values()):
        for lane in ["node-a-8core", "node-b-4core"]:
            if running[lane] is None and queues[lane]:
                spec = queues[lane].pop(0)
                apply_manifest(spec["manifest"])
                running[lane] = spec
                print(f"started: {spec['job']} on {lane}")
        time.sleep(1)
        for lane in ["node-a-8core", "node-b-4core"]:
            if running[lane] is None:
                continue
            #cehck if node is free and current phase
            phase = run(["kubectl", "get", "pods", "--selector=job-name=" + running[lane]["job"], "-o", "jsonpath={.items[0].status.phase}"], check=False).stdout.strip() or "Unknown"
            if phase == "Succeeded":
                print(f"finished: {running[lane]['job']} on {lane}")
                running[lane] = None
            elif phase == "Failed":
                raise RuntimeError(f"Job failed: {running[lane]['job']} on {lane}")

    print("\nstep 5: Stop the measurement & copy results")
    #shut down mcperf
    ssh(nodes["client-measure"], textwrap.dedent(f"""
        if [ -f {remote_pid_path} ]; then
          PID=$(cat {remote_pid_path})
          kill -TERM "$PID" 2>/dev/null || true
          rm -f {remote_pid_path}
        fi
    """).strip(), check=False)
    time.sleep(1)
    scp_from(nodes["client-measure"], remote_mcperf_path, run_dir / "mcperf.txt")

    print("\nstep 6: Save kubectl pod")
    pods_path = run_dir / "pods.json"
    pods_path.write_text(run(["kubectl", "get", "pods", "-o", "json"]).stdout)


if __name__ == "__main__":
    raise SystemExit(main())
