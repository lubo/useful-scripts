import asyncio
from typing import Any, Self

from bookmarkmgr.asyncio import ForgivingTaskGroup
from bookmarkmgr.cronet._cronet import ffi, lib
from bookmarkmgr.cronet.errors import NotContextManagerError
from bookmarkmgr.cronet.types import Executor, Runnable


@ffi.def_extern()
def _executor_execute(executor: Executor, runnable: Runnable) -> None:
    manager: ExecutorManager = ffi.from_handle(
        lib.Cronet_Executor_GetClientContext(executor),
    )
    manager.submit_runnable(runnable)


def _process_runnable(runnable: Runnable) -> None:
    try:
        lib.Cronet_Runnable_Run(runnable)
    finally:
        lib.Cronet_Runnable_Destroy(runnable)


class ExecutorManager:
    def __init__(self) -> None:
        self._executor: Executor | None = None
        self._handle = ffi.new_handle(self)
        self._task_group: ForgivingTaskGroup | None = None

    async def __aenter__(self) -> Self:
        if self._executor is None:
            self._executor = lib.Cronet_Executor_CreateWith(
                lib._executor_execute,  # noqa: SLF001
            )
            lib.Cronet_Executor_SetClientContext(self._executor, self._handle)

        if self._task_group is None:
            self._task_group = ForgivingTaskGroup()
            await self._task_group.__aenter__()

        self._processing_allowed = True

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        *args: Any,  # noqa: PYI036
        **kwargs: Any,
    ) -> None:
        self._processing_allowed = False

        try:
            if self._task_group is not None:
                await self._task_group.__aexit__(exc_type, *args, **kwargs)
                self._task_group = None
        finally:
            if self._executor is not None:
                lib.Cronet_Executor_Destroy(self._executor)
                self._executor = None

    def submit_runnable(self, runnable: Runnable) -> None:
        if self._task_group is None:
            message = "ExecutorManager has not been entered"
            raise RuntimeError(message)

        if not self._processing_allowed:
            lib.Cronet_Runnable_Destroy(runnable)
            return

        self._task_group.create_task(
            asyncio.to_thread(
                _process_runnable,
                runnable,
            ),
        )

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            raise NotContextManagerError

        return self._executor
