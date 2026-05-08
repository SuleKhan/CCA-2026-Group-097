from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Sequence
import urllib.parse

LOG_STRING = "{timestamp} {event} {job_name} {args}"


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
    def __init__(self, log_dir: str | Path = ".") -> None:
        start_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file_name = str(Path(log_dir) / f"log{start_date}.txt")
        self.file = open(self.file_name, "w", buffering=1)
        self._closed = False
        self._log("start", Job.SCHEDULER)

    def _ensure_job(self, job: Job) -> None:
        if job == Job.SCHEDULER:
            raise ValueError("Do not use job lifecycle methods for SCHEDULER")

    def _log(self, event: str, job_name: Job | str, args: str = "") -> None:
        if self._closed:
            return
        name = job_name if isinstance(job_name, str) else job_name.value
        self.file.write(
            LOG_STRING.format(
                timestamp=datetime.now().isoformat(),
                event=event,
                job_name=name,
                args=args,
            ).strip()
            + "\n"
        )

    def job_start(
        self,
        job: Job,
        initial_cores: Sequence[int | str],
        initial_threads: int,
    ) -> None:
        self._ensure_job(job)
        cores = "[" + ",".join(str(i) for i in initial_cores) + "]"
        self._log("start", job, f"{cores} {initial_threads}")

    def job_end(self, job: Job) -> None:
        self._ensure_job(job)
        self._log("end", job)

    def update_cores(self, job: Job, cores: Sequence[int | str]) -> None:
        self._ensure_job(job)
        self._log("update_cores", job, "[" + ",".join(str(i) for i in cores) + "]")

    def job_pause(self, job: Job) -> None:
        self._ensure_job(job)
        self._log("pause", job)

    def job_unpause(self, job: Job) -> None:
        self._ensure_job(job)
        self._log("unpause", job)

    def custom_event(self, job: Job, comment: str) -> None:
        self._log("custom", job, urllib.parse.quote_plus(comment))

    def end(self) -> None:
        if self._closed:
            return
        self._log("end", Job.SCHEDULER)
        self.file.flush()
        self.file.close()
        self._closed = True

    def get_file_name(self) -> str:
        return self.file_name
