import docker
import psutil
import subprocess
import time

from datetime import datetime
from scheduler_logger import SchedulerLogger, Job

# =========================
# CONFIG
# =========================

CHECK_INTERVAL = 5

TOTAL_CORES = [0, 1, 2, 3]
MEMCACHED_CORES = [0, 1, 2]
JOB_CORES = [3]

THREADS = 8

# Get memcached PID
MEMCACHED_PID = int(subprocess.getoutput("pidof memcached").split()[0])

client = docker.from_env()
logger = SchedulerLogger()

CPU_LOG_FILE = "cpu_usage.log"

# =========================
# MEMCACHED CONTROL
# =========================

def log_cpu(cpu_percent):
    timestamp = datetime.now().isoformat()
    with open(CPU_LOG_FILE, "a") as f:
        f.write(f"{timestamp},{cpu_percent:.2f}\n")

def set_memcached_cores(cores):
    core_str = ",".join(map(str, cores))
    cmd = f"sudo taskset -a -cp {core_str} {MEMCACHED_PID}"
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL)

    logger.update_cores(Job.MEMCACHED, cores)


# =========================
# DOCKER JOB CONTROL
# =========================

def start_job(job_enum, image, cores, threads):
    core_str = ",".join(map(str, cores))

    container = client.containers.run(
        image,
        command=f"./run -a run -S parsec -p {job_enum.value} -i native -n {threads}",
        detach=True,
        name=job_enum.value,
        cpuset_cpus=core_str,
        remove=False
    )

    logger.job_start(job_enum, cores, threads)
    return container


def update_job_cores(container, job_enum, cores):
    core_str = ",".join(map(str, cores))
    container.update(cpuset_cpus=core_str)

    logger.update_cores(job_enum, cores)


def stop_job(container, job_enum):
    container.kill()
    logger.job_end(job_enum)


# =========================
# JOB DEFINITIONS
# =========================

JOB_MAP = [
    (Job.BLACKSCHOLES, "anakli/cca:parsec_blackscholes"),
    (Job.CANNEAL, "anakli/cca:parsec_canneal"),
    (Job.FREQMINE, "anakli/cca:parsec_freqmine"),
    (Job.RADIX, "anakli/cca:splash2x_radix"),
    (Job.STREAMCLUSTER, "anakli/cca:parsec_streamcluster"),
    (Job.VIPS, "anakli/cca:parsec_vips"),
    (Job.BARNES, "anakli/cca:splash2x_barnes"),
]

job_queue = JOB_MAP.copy()
running_jobs = []

# =========================
# CPU MONITORING
# =========================

def get_cpu():
    return psutil.cpu_percent(interval=1)


# =========================
# SCHEDULER
# =========================

def scheduler():

    # Start memcached on 1 core
    logger.job_start(Job.MEMCACHED, [str(core) for core in MEMCACHED_CORES], THREADS)
    set_memcached_cores(MEMCACHED_CORES)

    # Run jobs sequentially
    for job_enum, image in JOB_MAP:
        print(f"Starting job: {job_enum.value}")

        container = start_job(job_enum, image, [str(core) for core in JOB_CORES], threads=THREADS)

        # Wait until job finishes
        while True:
            cpu = psutil.cpu_percent(interval=None)
            log_cpu(cpu)
            container.reload()
            if container.status == "exited":
                logger.job_end(job_enum)
                print(f"Finished job: {job_enum.value}")
                break
            time.sleep(2)

    logger.end()



# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()