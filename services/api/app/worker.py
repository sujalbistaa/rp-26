"""The RQ worker. Takes the Redis URL from settings, so TLS options stay in one place."""

from redis import Redis
from rq import Worker

from app.config import get_settings
from app.pipeline import get_ml


def main() -> None:
    if get_settings().require_gpu:
        get_ml()
    Worker(["rkl"], connection=Redis.from_url(get_settings().redis_url)).work()


if __name__ == "__main__":
    main()
