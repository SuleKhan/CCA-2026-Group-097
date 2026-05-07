import docker
import psutil
import subprocess
import time
from datetime import datetime

from scheduler_logger import SchedulerLogger, Job

# =========================
# CONFIG
# =========================

CHECK_INTERVAL = 0.2
COOLDOWN_SEC = 3
CPU_THRESHOLD = 60

TOTAL_CORES = [0, 1, 2, 3]
MEMCACHED_CORES = [0, 1]
JOB_CORES = [2, 3]

THREADS = 8

# Get memcached PID
MEMCACHED_PID = int(subprocess.getoutput("pidof memcached").split()[0])

CURRENT_MEMCACHED_CORES = None

CPU_LOG_FILE = "cpu_usage.log"

client = docker.from_env()
logger = SchedulerLogger()

# =========================
# MEMCACHED CONTROL
# =========================

def log_cpu(cpu_percent):
    timestamp = datetime.now().isoformat()
    with open(CPU_LOG_FILE, "a") as f:
        f.write(f"{timestamp},{cpu_percent:.2f}\n")

def set_memcached_cores(cores):
    global CURRENT_MEMCACHED_CORES

    if cores == CURRENT_MEMCACHED_CORES:
        return

    core_str = ",".join(map(str, cores))
    cmd = f"sudo taskset -a -cp {core_str} {MEMCACHED_PID}"
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL)

    logger.update_cores(Job.MEMCACHED, cores)
    CURRENT_MEMCACHED_CORES = cores


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

# =========================
# SCHEDULER
# =========================

def scheduler():

    psutil.cpu_percent(interval=None)

    # memcached init
    logger.job_start(Job.MEMCACHED, [str(c) for c in MEMCACHED_CORES], THREADS)
    set_memcached_cores(MEMCACHED_CORES)

    job_queue = JOB_MAP.copy()

    container = None
    current_job = None
    using_core2 = False

    last_switch_time = 0

    def apply_cores(container, cores):
        core_str = ",".join(map(str, cores))
        container.update(cpuset_cpus=core_str)

    # -------------------------
    # START FIRST JOB
    # -------------------------
    if job_queue:
        current_job, image = job_queue.pop(0)

        print(f"Starting {current_job.value} on core 3")

        container = start_job(
            current_job,
            image,
            [3],
            threads=THREADS
        )

    # -------------------------
    # MAIN LOOP
    # -------------------------
    while container is not None:

        container.reload()

        # job finished
        if container.status == "exited":

            logger.job_end(current_job)
            print(f"Finished {current_job.value}")

            if job_queue:
                current_job, image = job_queue.pop(0)

                print(f"Starting {current_job.value}")

                container = start_job(
                    current_job,
                    image,
                    [3],
                    threads=THREADS
                )

                using_core2 = False
            else:
                break

        # -------------------------
        # CPU CONTROL (RAW + COOLDOWN)
        # -------------------------
        cpu = psutil.cpu_percent(interval=None)
        now = time.time()
        log_cpu(cpu)

        if cpu > CPU_THRESHOLD and (now - last_switch_time) > COOLDOWN_SEC:

            # give cores to memcached
            set_memcached_cores([0, 1, 2])

            # shrink job if needed
            if using_core2:
                print(f"Shrinking {current_job.value} → core [3] (CPU {cpu:.1f}%)")
                apply_cores(container, [3])
                using_core2 = False
                logger.update_cores(current_job, [3])

            last_switch_time = now

        elif cpu <= CPU_THRESHOLD and (now - last_switch_time) > COOLDOWN_SEC:

            # return memcached to normal
            set_memcached_cores([0, 1])

            # expand job
            if not using_core2:
                print(f"Expanding {current_job.value} → cores [2,3] (CPU {cpu:.1f}%)")
                apply_cores(container, [2, 3])
                using_core2 = True
                logger.update_cores(current_job, [2, 3])

            last_switch_time = now

        time.sleep(CHECK_INTERVAL)

    logger.end()


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()