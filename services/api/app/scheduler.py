"""Queues the daily probe purge. Runs as its own small process in compose."""

import time

from app.jobs import purge_probes, queue

INTERVAL_SECONDS = 24 * 3600


def main() -> None:
    while True:
        queue().enqueue(purge_probes, job_timeout="1h")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
