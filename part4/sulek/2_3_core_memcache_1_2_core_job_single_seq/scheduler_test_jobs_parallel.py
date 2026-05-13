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

HIGH_THRESHOLD = 70
LOW_THRESHOLD = 70

COOLDOWN_SEC = 3

HIGH_CPU_CYCLES_REQUIRED = 3
LOW_CPU_CYCLES_REQUIRED = 5

BASE_MEMCACHED_CORES = [0, 1]
ALL_CORES = [0, 1, 2, 3]

THREADS_MEMCACHED = 8

CPU_LOG_FILE = "cpu_usage.log"

# =========================
# INIT
# =========================

client = docker.from_env()
logger = SchedulerLogger()

MEMCACHED_PID = int(subprocess.getoutput("pidof memcached").split()[0])

CURRENT_MEMCACHED_CORES = None

# =========================
# JOB QUEUES
# =========================

CORE3_QUEUE = [
    {
        "job": Job.BARNES,
        "image": "anakli/cca:splash2x_barnes",
        "cores": [3],
        "threads": 8
    },
    {
        "job": Job.STREAMCLUSTER,
        "image": "anakli/cca:parsec_streamcluster",
        "cores": [3],
        "threads": 8
    },
    {
        "job": Job.FREQMINE,
        "image": "anakli/cca:parsec_freqmine",
        "cores": [3],
        "threads": 8
    },
    {
        "job": Job.VIPS,
        "image": "anakli/cca:parsec_vips",
        "cores": [3],
        "threads": 8
    },
]

CORE2_QUEUE = [
    {
        "job": Job.BLACKSCHOLES,
        "image": "anakli/cca:parsec_blackscholes",
        "cores": [2],
        "threads": 8
    },
    {
        "job": Job.CANNEAL,
        "image": "anakli/cca:parsec_canneal",
        "cores": [2],
        "threads": 8
    },
    {
        "job": Job.RADIX,
        "image": "anakli/cca:splash2x_radix",
        "cores": [2],
        "threads": 8
    },
]

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

    subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    logger.update_cores(Job.MEMCACHED, cores)

    CURRENT_MEMCACHED_CORES = cores.copy()

    print(f"[memcached] cores -> {cores}")


def update_memcached_cores(core2_free, core3_free):
    """
    Decide memcached allocation based on free cores.
    """

    cores = BASE_MEMCACHED_CORES.copy()

    if core2_free:
        cores.append(2)

    if core3_free:
        cores.append(3)

    set_memcached_cores(sorted(cores))


# =========================
# JOB CONTROL
# =========================

def start_job(cfg, pipeline="unknown"):

    core_str = ",".join(map(str, cfg["cores"]))

    print(
        f"Starting Job {cfg['job'].value} "
        f"on Core {cfg['cores']} "
        f"with {cfg['threads']} Threads"
    )

    container = client.containers.run(
        cfg["image"],
        command=(
            f"./run -a run -S parsec "
            f"-p {cfg['job'].value} "
            f"-i native "
            f"-n {cfg['threads']}"
        ),
        detach=True,
        name=cfg["job"].value,
        cpuset_cpus=core_str,
        remove=False
    )

    logger.job_start(
        cfg["job"],
        cfg["cores"],
        cfg["threads"]
    )

    return container


def apply_cores(container, cores):

    if container is None:
        return

    core_str = ",".join(map(str, cores))

    container.update(cpuset_cpus=core_str)


def advance(container, cfg, queue, core_name):

    if container is None:
        return None, cfg

    try:
        container.reload()

    except Exception as e:
        print(f"Reload failed: {e}")
        return None, None

    if container.status != "exited":
        return container, cfg

    logger.job_end(cfg["job"])

    print(f"Finished Job {cfg['job'].value}")

    try:
        container.remove(force=True)

    except:
        pass

    if not queue:
        print(f"No more jobs on {core_name}")
        return None, None

    next_cfg = queue.pop(0)

    print(
        f"Starting Job {next_cfg['job'].value} "
        f"on Core {next_cfg['cores']} "
        f"with {next_cfg['threads']} Threads"
    )

    new_container = start_job(
        next_cfg,
        pipeline=core_name
    )

    return new_container, next_cfg


# =========================
# SCHEDULER
# =========================

def scheduler():

    # initialize psutil baseline
    psutil.cpu_percent(interval=None)

    # -------------------------
    # CPU state tracking
    # -------------------------

    high_cpu_cycles = 0
    low_cpu_cycles = 0

    last_switch_time = 0

    # -------------------------
    # memcached init
    # -------------------------

    logger.job_start(
        Job.MEMCACHED,
        BASE_MEMCACHED_CORES,
        THREADS_MEMCACHED
    )

    set_memcached_cores(BASE_MEMCACHED_CORES)

    # -------------------------
    # queues
    # -------------------------

    core2_queue = CORE2_QUEUE.copy()
    core3_queue = CORE3_QUEUE.copy()

    # -------------------------
    # containers
    # -------------------------

    container2 = None
    cfg2 = None

    container3 = None
    cfg3 = None

    # -------------------------
    # start first jobs
    # -------------------------

    if core2_queue:
        cfg2 = core2_queue.pop(0)
        container2 = start_job(cfg2)

    if core3_queue:
        cfg3 = core3_queue.pop(0)
        container3 = start_job(cfg3)

    # -------------------------
    # scheduler state
    # -------------------------

    core2_paused = False

    # =========================
    # MAIN LOOP
    # =========================

    while True:

        # -------------------------
        # advance pipelines
        # -------------------------

        container2, cfg2 = advance(
            container2,
            cfg2,
            core2_queue,
            "core2"
        )

        container3, cfg3 = advance(
            container3,
            cfg3,
            core3_queue,
            "core3"
        )

        # -------------------------
        # reset state if needed
        # -------------------------

        if container2 is None:
            core2_paused = False

        # -------------------------
        # completion
        # -------------------------

        if container2 is None and container3 is None:

            print("All jobs finished")

            set_memcached_cores(ALL_CORES)

            logger.end()

            print("Scheduler complete")

            break

        # -------------------------
        # free-core detection
        # -------------------------

        core2_free = (
            container2 is None or core2_paused
        )

        core3_free = (
            container3 is None
        )

        # -------------------------
        # memcached update
        # -------------------------

        update_memcached_cores(
            core2_free,
            core3_free
        )

        # -------------------------
        # CPU sampling
        # -------------------------

        cpu = psutil.cpu_percent(interval=None)

        now = time.time()

        log_cpu(cpu)

        # -------------------------
        # CPU stability tracking
        # -------------------------

        if cpu > HIGH_THRESHOLD:

            high_cpu_cycles += 1
            low_cpu_cycles = 0

        else:

            low_cpu_cycles += 1
            high_cpu_cycles = 0

        # -------------------------
        # PAUSE core2 pipeline
        # -------------------------

        if (
            container2 is not None
            and not core2_paused
            and high_cpu_cycles >= HIGH_CPU_CYCLES_REQUIRED
            and (now - last_switch_time) > COOLDOWN_SEC
        ):

            print(
                f"Pausing core2 pipeline "
                f"(CPU {cpu:.1f}%, "
                f"{high_cpu_cycles} cycles)"
            )

            try:

                container2.pause()

                core2_paused = True

                update_memcached_cores(
                    True,
                    core3_free
                )

                last_switch_time = now

                high_cpu_cycles = 0

            except Exception as e:

                print(f"Pause failed: {e}")

        # -------------------------
        # RESUME core2 pipeline
        # -------------------------

        elif (
            container2 is not None
            and core2_paused
            and low_cpu_cycles >= LOW_CPU_CYCLES_REQUIRED
            and (now - last_switch_time) > COOLDOWN_SEC
        ):

            print(
                f"Resuming core2 pipeline "
                f"(CPU {cpu:.1f}%, "
                f"{low_cpu_cycles} cycles)"
            )

            try:

                container2.unpause()

                core2_paused = False

                update_memcached_cores(
                    False,
                    core3_free
                )

                last_switch_time = now

                low_cpu_cycles = 0

            except Exception as e:

                print(f"Resume failed: {e}")

        time.sleep(CHECK_INTERVAL)


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    scheduler()