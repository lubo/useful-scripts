import asyncio

from .logging import get_logger

logger = get_logger()


class ForgivingTaskGroup(asyncio.TaskGroup):
    """TaskGroup that doesn't fail fast on task failure."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._parent_cancel_requested = True

    def _on_task_done(self, task, *args, **kwargs):
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.critical(
                "Unhandled error occurred in task '%s': %s: %s",
                task.get_name(),
                type(exc).__name__,
                exc,
            )

        return super()._on_task_done(task, *args, **kwargs)


# https://github.com/mjpieters/aiolimiter and
# https://github.com/ArtyomKozyrev8/BucketRateLimiter provide inadequate
# performance. See https://github.com/mjpieters/aiolimiter/issues/73.
class RateLimiter:
    def __init__(self, limit, period=60):
        self.period = period

        self._semaphore = asyncio.Semaphore(limit)
        self._release_tasks = set()

    async def __aenter__(self):
        await self._semaphore.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        task = asyncio.create_task(self._release())
        self._release_tasks.add(task)
        task.add_done_callback(self._release_tasks.remove)

    async def _release(self):
        await asyncio.sleep(self.period)

        self._semaphore.release()

    def close(self):
        for task in self._release_tasks:
            task.cancel()


class RateLimiterMixin:
    def __init__(
        self,
        *args,
        rate_limit,
        rate_limit_period=60,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self._rate_limiter = RateLimiter(rate_limit, rate_limit_period)
