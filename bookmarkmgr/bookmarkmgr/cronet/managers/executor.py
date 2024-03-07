import asyncio
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Any, Self, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

from bookmarkmgr.cronet._cronet import ffi, lib
from bookmarkmgr.cronet.errors import NotContextManagerError
from bookmarkmgr.cronet.types import Executor, Runnable


@ffi.def_extern()
def _executor_execute(executor: Executor, runnable: Runnable) -> None:
    manager: "ExecutorManager" = ffi.from_handle(
        lib.Cronet_Executor_GetClientContext(executor),
    )
    manager.enqueue_runnable(runnable)


class ExecutorManager:
    def __init__(self) -> None:
        self._handle = ffi.new_handle(self)
        self._queue: Queue[Runnable | None] = Queue()
        self._worker: Awaitable[None] | None = None
        self._executor: Executor | None = None

    async def __aenter__(self) -> Self:
        if self._executor is None:
            self._executor = lib.Cronet_Executor_CreateWith(
                lib._executor_execute,  # noqa: SLF001
            )
            lib.Cronet_Executor_SetClientContext(self._executor, self._handle)

        self._processing_allowed = True

        if self._worker is None:
            self._worker = asyncio.create_task(self._spawn_worker_thread())

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        *args: Any,  # noqa: PYI036
        **kwargs: Any,
    ) -> None:
        if exc_type is not None:
            self.shutdown()

        try:
            if self._worker is not None:
                await self._worker
        finally:
            if self._executor is not None:
                lib.Cronet_Executor_Destroy(self._executor)
                self._executor = None

            self._worker = None

    async def _spawn_worker_thread(self) -> None:
        with ThreadPoolExecutor() as pool:
            await asyncio.get_running_loop().run_in_executor(
                pool,
                self._worker_loop,
            )

    def _worker_loop(self) -> None:
        while (runnable := self._queue.get()) is not None:
            try:
                if self._processing_allowed:
                    lib.Cronet_Runnable_Run(runnable)
            finally:
                lib.Cronet_Runnable_Destroy(runnable)

    def enqueue_runnable(self, runnable: Runnable) -> None:
        self._queue.put_nowait(runnable)

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            raise NotContextManagerError

        return self._executor

    def shutdown(self) -> None:
        self._processing_allowed = False
        self._queue.put_nowait(None)
