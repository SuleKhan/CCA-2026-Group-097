from datetime import datetime
from enum import Enum
import urllib.parse

try:
    import psutil
except ImportError:  # Keeps the module importable, but CPU logging needs psutil.
    psutil = None


LOG_STRING = "{timestamp} {event} {job_name} {args}"
CPU_LOG_HEADER = "timestamp cpu_percent\n"
PER_CORE_CPU_LOG_HEADER = "timestamp core0 core1 core2 core3\n"


class Job(Enum):
    SCHEDULER = "scheduler"
    MEMCACHED = "memcached"
    BARNES = "barnes"
    BLACKSCHOLES = "blackscholes"
    CANNEAL = "canneal"
    FREQMINE = "freqmine"
    RADIX = "radix"
    STREAMCLUSTER = "streamcluster"
    VIPS = "vips"


class SchedulerLogger:
    def __init__(self):
        start_date = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.file_name = f"log{start_date}.txt"
        self.cpu_file_name = f"cpu_log{start_date}.txt"
        self.per_core_cpu_file_name = f"per_core_cpu_log{start_date}.txt"

        self.file = open(self.file_name, "w")
        self.cpu_file = open(self.cpu_file_name, "w")
        self.per_core_cpu_file = open(self.per_core_cpu_file_name, "w")

        self.cpu_file.write(CPU_LOG_HEADER)
        self.per_core_cpu_file.write(PER_CORE_CPU_LOG_HEADER)

        self._log("start", Job.SCHEDULER)

    def _log(self, event: str, job_name: Job, args: str = "") -> None:
        if isinstance(job_name, str):
            self.file.write(
                LOG_STRING.format(
                    timestamp=datetime.now().isoformat(),
                    event=event,
                    job_name=job_name,
                    args=args,
                ).strip()
                + "\n"
            )
        else:
            self.file.write(
                LOG_STRING.format(
                    timestamp=datetime.now().isoformat(),
                    event=event,
                    job_name=job_name.value,
                    args=args,
                ).strip()
                + "\n"
            )

    def job_start(self, job: Job, initial_cores: list[str], initial_threads: int) -> None:
        assert job != Job.SCHEDULER, "You don't have to log SCHEDULER here"

        self._log("start", job, "[" + (",".join(str(i) for i in initial_cores)) + "] " + str(initial_threads))

    def job_end(self, job: Job) -> None:
        assert job != Job.SCHEDULER, "You don't have to log SCHEDULER here"

        self._log("end", job)

    def update_cores(self, job: Job, cores: list[str]) -> None:
        assert job != Job.SCHEDULER, "You don't have to log SCHEDULER here"

        self._log("update_cores", job, "[" + (",".join(str(i) for i in cores)) + "]")

    def job_pause(self, job: Job) -> None:
        assert job != Job.SCHEDULER, "You don't have to log SCHEDULER here"

        self._log("pause", job)

    def job_unpause(self, job: Job) -> None:
        assert job != Job.SCHEDULER, "You don't have to log SCHEDULER here"

        self._log("unpause", job)

    def custom_event(self, job: Job, comment: str):
        self._log("custom", job, urllib.parse.quote_plus(comment))

    def log_cpu_usage(self, interval: float = 0.1) -> None:
        """Log current total CPU usage and per-core CPU usage.

        Creates one row in cpu_log*.txt:
            timestamp cpu_percent

        Creates one row in per_core_cpu_log*.txt:
            timestamp core0 core1 core2 core3
        """
        if psutil is None:
            raise ImportError("CPU logging requires psutil. Install it with: pip install psutil")

        timestamp = datetime.now().isoformat()
        total_cpu_percent = psutil.cpu_percent(interval=interval, percpu=False)
        per_core_cpu_percent = psutil.cpu_percent(interval=None, percpu=True)

        if len(per_core_cpu_percent) < 4:
            raise RuntimeError(f"Expected at least 4 CPU cores, got {len(per_core_cpu_percent)}")

        first_four_cores = per_core_cpu_percent[:4]

        self.cpu_file.write(f"{timestamp} {total_cpu_percent:.2f}\n")
        self.per_core_cpu_file.write(
            f"{timestamp} " + " ".join(f"{usage:.2f}" for usage in first_four_cores) + "\n"
        )

        self.cpu_file.flush()
        self.per_core_cpu_file.flush()

    def end(self) -> None:
        self._log("end", Job.SCHEDULER)

        self.file.flush()
        self.cpu_file.flush()
        self.per_core_cpu_file.flush()

        self.file.close()
        self.cpu_file.close()
        self.per_core_cpu_file.close()

    def get_file_name(self):
        return self.file_name

    def get_cpu_file_name(self):
        return self.cpu_file_name

    def get_per_core_cpu_file_name(self):
        return self.per_core_cpu_file_name
