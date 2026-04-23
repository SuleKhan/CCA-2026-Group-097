import os
import time
import itertools
import subprocess

# -----------------------
# config
# -----------------------
CONFIG_FILE = "/etc/memcached.conf"
RESULTS_DIR = "./results"
CORES = [0, 1, 2]

SSH_KEY = os.path.expanduser("~/.ssh/cloud-computing")

os.makedirs(RESULTS_DIR, exist_ok=True)

# -----------------------
# machines
# -----------------------
memcached = RemoteHost(
    "memcached",
    "ubuntu",
    external_ip="10.0.16.4",
    internal_ip="10.0.16.4",
    ssh_key=SSH_KEY,
)

measure = RemoteHost(
    "measure",
    "ubuntu",
    external_ip="34.14.100.95",
    internal_ip="34.14.100.95",
    ssh_key=SSH_KEY,
)

agent = RemoteHost(
    "agent",
    "ubuntu",
    external_ip="X.X.X.X",
    internal_ip="X.X.X.X",
    ssh_key=SSH_KEY,
)


# -----------------------
# core runner
# -----------------------
def run(cmd, timeout=120):
    return subprocess.run(
        cmd,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class RemoteHost:
    def __init__(self, name, user, external_ip, internal_ip, ssh_key):
        self.name = name
        self.user = user
        self.external_ip = external_ip
        self.internal_ip = internal_ip
        self.ssh_key = ssh_key

    def _ssh_cmd(self, cmd, use_internal=False):
        ip = self.internal_ip if use_internal else self.external_ip
        return (
            f"ssh -i {self.ssh_key} "
            f"-o StrictHostKeyChecking=no "
            f"{self.user}@{ip} "
            f"bash -lc \"{cmd}\""
        )

    def run(self, cmd, use_internal=False):
        full_cmd = self._ssh_cmd(cmd, use_internal)
        print(f"[{self.name}] {cmd}")
        return run(full_cmd)

    def popen(self, cmd, use_internal=False):
        full_cmd = self._ssh_cmd(cmd, use_internal)
        print(f"[{self.name}] (async) {cmd}")
        return subprocess.Popen(full_cmd, shell=True)

# sanity check
assert agent.external_ip != "X.X.X.X", "Agent external IP not set"


# -----------------------
# setup scripts
# -----------------------
SETUP_SCRIPTS = [
    (memcached, "CCA-2026-Group-097/part4/tboschung/setup_cluster.sh"),
    (measure,   "CCA-2026-Group-097/part4/tboschung/setup_mcperf_measure.sh"),
    (agent,     "CCA-2026-Group-097/part4/tboschung/setup_mcperf_agent.sh"),
]


def run_setup():
    print("Running setup on all machines...\n")

    procs = [
        host.popen(f"bash {script}")
        for host, script in SETUP_SCRIPTS
    ]

    for p in procs:
        ret = p.wait()
        if ret != 0:
            raise RuntimeError(f"Setup failed with exit code {ret}")

    print("\nSetup complete.\n")


# -----------------------
# memcached control
# -----------------------
def set_threads(t):
    memcached.run(
        f"sudo sed -i 's/^-t .*/-t {t}/' {CONFIG_FILE} "
        f"|| echo '-t {t}' | sudo tee -a {CONFIG_FILE}"
    )
    memcached.run("sudo systemctl restart memcached")
    time.sleep(2)


def get_pid():
    for _ in range(5):
        result = memcached.run("pidof memcached")
        pids = result.stdout.strip().split()
        if pids:
            return pids[0]
        time.sleep(0.5)
    raise RuntimeError("Could not find memcached PID")


def set_cores(c):
    pid = get_pid()
    core_list = ",".join(map(str, CORES[:c]))
    memcached.run(f"sudo taskset -cp {core_list} {pid}")


# -----------------------
# mcperf
# -----------------------
def warmup():
    cmd = (
        f"./mcperf -s {memcached.internal_ip} "
        f"--noload -T 2 -C 2 -D 2 -Q 100 -c 2 -t 2 "
        f"--scan 1000:5000:1000 > /dev/null"
    )
    measure.run(cmd, use_internal=True)


def benchmark(output_file):
    cmd = (
        f"./mcperf -s {memcached.internal_ip} "
        f"--noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 2 "
        f"--scan 5000:125000:10000"
    )
    result = measure.run(cmd, use_internal=True)

    with open(output_file, "w") as f:
        f.write(result.stdout)


# -----------------------
# experiment
# -----------------------
def run_experiments():
    for t, c in itertools.product([1, 2, 3], [1, 2, 3]):
        for run_id in range(1, 4):

            print(f"\n=== Running T={t}, C={c}, run={run_id} ===")

            set_threads(t)
            set_cores(c)

            warmup()

            outfile = f"{RESULTS_DIR}/T{t}_C{c}_run{run_id}.txt"
            benchmark(outfile)

            time.sleep(2)


# -----------------------
# main
# -----------------------
if __name__ == "__main__":
    run_setup()
    run_experiments()