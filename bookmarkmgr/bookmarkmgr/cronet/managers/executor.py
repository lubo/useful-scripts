from concurrent.futures import ThreadPoolExecutor

from bookmarkmgr.cronet._cronet import ffi, lib
from bookmarkmgr.cronet.types import Executor, Runnable


@ffi.def_extern()
def _executor_execute(executor: Executor, runnable: Runnable) -> None:
    manager: ExecutorManager = ffi.from_handle(
        lib.Cronet_Executor_GetClientContext(executor),
    )
    manager.enqueue_runnable(runnable)


def _process_runnable(runnable: Runnable) -> None:
    try:
        lib.Cronet_Runnable_Run(runnable)
    finally:
        lib.Cronet_Runnable_Destroy(runnable)


class ExecutorManager:
    def __init__(self) -> None:
        self._handle = ffi.new_handle(self)

        self._cronet_executor: Executor = lib.Cronet_Executor_CreateWith(
            lib._executor_execute,  # noqa: SLF001
        )
        lib.Cronet_Executor_SetClientContext(
            self._cronet_executor,
            self._handle,
        )

        self._thread_executor = ThreadPoolExecutor()

    def enqueue_runnable(self, runnable: Runnable) -> None:
        self._thread_executor.submit(
            _process_runnable,
            runnable,
        )

    @property
    def executor(self) -> Executor:
        return self._cronet_executor

    def shutdown(self) -> None:
        try:
            self._thread_executor.shutdown(wait=False, cancel_futures=True)
        finally:
            lib.Cronet_Executor_Destroy(self._cronet_executor)
