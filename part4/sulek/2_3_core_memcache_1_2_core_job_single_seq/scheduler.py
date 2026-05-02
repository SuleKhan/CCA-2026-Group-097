import docker
import psutil
import subprocess
import time

from scheduler_logger import SchedulerLogger, Job

# =========================
# CONFIG
# =========================

CHECK_INTERVAL = 0

TOTAL_CORES = [0, 1, 2, 3]
MEMCACHED_CORES = [0, 1]
JOB_CORES = [2, 3]

THREADS = 8

# Get memcached PID
MEMCACHED_PID = int(subprocess.getoutput("pidof memcached").split()[0])

client = docker.from_env()
logger = SchedulerLogger()

# =========================
# MEMCACHED CONTROL
# =========================

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

    # -------------------------
    # MEMCACHED (always on cores 0-1)
    # -------------------------
    logger.job_start(Job.MEMCACHED, [str(core) for core in MEMCACHED_CORES], THREADS)
    set_memcached_cores(MEMCACHED_CORES)

    job_queue = JOB_MAP.copy()

    container = None
    current_job = None
    using_core2 = False

    high_cpu_count = 0   # <-- HISTORY TRACKER

    def apply_cores(container, cores):
        core_str = ",".join(map(str, cores))
        container.update(cpuset_cpus=core_str)

    # -------------------------
    # START FIRST JOB (CORE 3 ONLY)
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

        # job finished → start next one
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
                high_cpu_count = 0   # reset history per job
            else:
                break

        # -------------------------
        # CPU ADAPTATION WITH HISTORY
        # -------------------------
        cpu = psutil.cpu_percent(interval=1)

        if cpu > 90:
            high_cpu_count += 1
        else:
            high_cpu_count = 0

        # -------------------------
        # HIGH LOAD STATE (protect memcached)
        # -------------------------
        if high_cpu_count >= 3:

            # 1. SHRINK job to 1 core
            if using_core2:
                print(f"Shrinking {current_job.value} → core [3] (CPU {cpu:.1f}%)")

                apply_cores(container, [3])
                using_core2 = False
                logger.update_cores(current_job, [3])

            # 2. EXPAND memcached (IMPORTANT)
            set_memcached_cores([0, 1, 2])
            logger.update_cores(Job.MEMCACHED, ["0", "1", "2"])

            high_cpu_count = 0   # reset after reaction

        # -------------------------
        # SHRINK POLICY
        # -------------------------
        else:
            # LOW LOAD STATE (give resources to job)
            # 1. shrink memcached back (recover cores)
            set_memcached_cores([0, 1])
            logger.update_cores(Job.MEMCACHED, ["0", "1"])

            # 2. expand job if needed
            if not using_core2:
                print(f"Expanding {current_job.value} → cores [2,3] (CPU {cpu:.1f}%)")

                apply_cores(container, [2, 3])
                using_core2 = True
                logger.update_cores(current_job, [2, 3])


        time.sleep(CHECK_INTERVAL)

    logger.end()



# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()