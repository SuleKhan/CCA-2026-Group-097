import docker
import psutil
import subprocess
import time

from scheduler_logger import SchedulerLogger, Job

# =========================
# CONFIG
# =========================

CHECK_INTERVAL = 5
TOTAL_CORES = [0, 1, 2, 3]

# Get memcached PID
MEMCACHED_PID = int(subprocess.getoutput("pidof memcached").split()[0])

client = docker.from_env()
logger = SchedulerLogger()

# =========================
# MEMCACHED CONTROL
# =========================

def set_memcached_cores(cores):
    core_str = ",".join(map(str, cores))
    cmd = f"taskset -a -cp {core_str} {MEMCACHED_PID}"
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

    # IMPORTANT: log memcached start
    memcached_cores = [0, 1]
    logger.job_start(Job.MEMCACHED, memcached_cores, 2)

    set_memcached_cores(memcached_cores)

    batch_cores = [2, 3]

    while True:
        cpu = get_cpu()
        print(f"CPU: {cpu}%")

        # =====================
        # POLICY
        # =====================

        if cpu > 80:
            logger.custom_event(Job.SCHEDULER, "High CPU → prioritize memcached")

            if len(memcached_cores) < 4 and batch_cores:
                memcached_cores.append(batch_cores.pop())
                set_memcached_cores(memcached_cores)

                for (c, job_enum) in running_jobs:
                    update_job_cores(c, job_enum, batch_cores)

        elif cpu < 50:
            logger.custom_event(Job.SCHEDULER, "Low CPU → give resources to batch")

            if len(batch_cores) < 3 and len(memcached_cores) > 1:
                batch_cores.append(memcached_cores.pop())
                set_memcached_cores(memcached_cores)

        # =====================
        # START JOBS
        # =====================

        if job_queue and len(running_jobs) < 2:
            job_enum, image = job_queue.pop(0)
            container = start_job(job_enum, image, batch_cores, threads=2)
            running_jobs.append((container, job_enum))

        # =====================
        # CLEANUP FINISHED
        # =====================

        still_running = []
        for (c, job_enum) in running_jobs:
            c.reload()
            if c.status == "exited":
                logger.job_end(job_enum)
            else:
                still_running.append((c, job_enum))

        running_jobs[:] = still_running

        # =====================
        # EXIT
        # =====================

        if not job_queue and not running_jobs:
            break

        time.sleep(CHECK_INTERVAL)

    logger.end()


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()