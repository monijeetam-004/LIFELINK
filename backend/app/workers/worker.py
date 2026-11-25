"""RQ worker entrypoint: ``python -m app.workers.worker``."""
from __future__ import annotations

from rq import Worker

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.core.redis import rq_redis


def main() -> None:
    # Phase I: the worker never installed the app's logging config, so the
    # job-level "email job ... sent/failed" lines (lifelink.email, with the
    # originating request id) were silently dropped in a real worker
    # process -- only RQ's own lines appeared. And RQ's default
    # log_job_description=True printed each job's full description: the
    # recipient's address in clear plus the start of the email body,
    # contradicting the masked-address logging the API process uses.
    configure_logging()
    import logging

    # Same line as the API's lifespan: both processes must use one provider.
    logging.getLogger("lifelink.email").info("mail transport: %s", settings.mail_summary())
    worker = Worker(
        [settings.rq_queue_name, settings.rq_sms_queue_name],
        connection=rq_redis,
        log_job_description=False,
    )
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
