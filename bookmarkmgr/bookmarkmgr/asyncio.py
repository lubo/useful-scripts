# ruff: noqa: A005

import asyncio
from asyncio import Semaphore, Task, TaskGroup
import time
from typing import Any

from .logging import get_logger

logger = get_logger()


class ForgivingTaskGroup(TaskGroup):
    """TaskGroup that doesn't fail fast on task failure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self._parent_cancel_requested = True

    def _on_task_done(
        self,
        task: Task[Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
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
    def __init__(self, limit: int, period: float = 60):
        self.period = period

        self._semaphore = Semaphore(limit)
        self._release_tasks: set[Task[None]] = set()

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(self, *args: object, **kwargs: Any) -> None:
        task = asyncio.create_task(self._release(self.period + time.time()))
        self._release_tasks.add(task)
        task.add_done_callback(self._release_task_done)

    async def _release(self, at: float) -> None:
        if (remaining := at - time.time()) > 0:
            await asyncio.sleep(remaining)

        self._semaphore.release()

    def _release_task_done(self, task: Task[None]) -> None:
        self._release_tasks.remove(task)

        if not task.cancelled() and task.done():
            task.result()

    def close(self) -> None:
        for task in self._release_tasks:
            task.cancel()


class RateLimiterMixin:
    def __init__(
        self,
        *args: Any,
        rate_limit: int,
        rate_limit_period: float = 60,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self._RateLimiterMixin_rate_limiter = RateLimiter(
            rate_limit,
            rate_limit_period,
        )
