import asyncio
from asyncio import Lock, Semaphore, Task, TaskGroup
from collections.abc import Callable
import random
import time
from typing import Any, cast, ParamSpec, TypeVar

from overrides import override

from .logging import get_logger

logger = get_logger()


class ForgivingTaskGroup(TaskGroup):
    """TaskGroup that doesn't fail fast on task failure."""

    _parent_cancel_requested: bool

    @override
    def _is_base_error(self, exc: BaseException) -> bool:
        return cast(
            "bool",
            super()._is_base_error(exc),  # type: ignore[misc]
        )

    @override
    def _on_task_done(
        self,
        task: Task[Any],
    ) -> None:
        if (
            task.cancelled()
            or (exc := task.exception()) is None
            or self._is_base_error(exc)
        ):
            super()._on_task_done(task)
            return

        logger.critical(
            "Unhandled error occurred in task '%s': %s: %s",
            task.get_name(),
            type(exc).__name__,
            exc,
        )

        parent_cancel_requested = self._parent_cancel_requested
        self._parent_cancel_requested = True

        super()._on_task_done(task)

        self._parent_cancel_requested = parent_cancel_requested


# https://github.com/mjpieters/aiolimiter and
# https://github.com/ArtyomKozyrev8/BucketRateLimiter provide inadequate
# performance. See https://github.com/mjpieters/aiolimiter/issues/73.
class RateLimiter:
    def __init__(self, limit: int, period: float = 60, jitter: float = 0):
        self.jitter = jitter
        self.period = period

        self._semaphore = Semaphore(limit)
        self._release_tasks: set[Task[None]] = set()

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(self, *args: object, **kwargs: Any) -> None:
        jitter = random.uniform(0, self.jitter)  # noqa: S311
        task = asyncio.create_task(
            self._release(time.time() + self.period + jitter),
        )
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


_GILED_CPU_THREAD_LOCK = Lock()

_P = ParamSpec("_P")
_R = TypeVar("_R")


# Runs CPU-bound tasks which don't release the GIL in a dedicated thread.
async def to_cpu_bound_giled_thread(
    func: Callable[_P, _R],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> _R:
    async with _GILED_CPU_THREAD_LOCK:
        return await asyncio.to_thread(func, *args, **kwargs)
