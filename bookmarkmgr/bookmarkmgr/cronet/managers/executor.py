import asyncio
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

from bookmarkmgr.cronet._cronet import ffi, lib


@ffi.def_extern()
def _executor_execute(executor, runnable):
    manager = ffi.from_handle(lib.Cronet_Executor_GetClientContext(executor))
    manager.enqueue_runnable(runnable)


class ExecutorManager:
    def __init__(self):
        self._handle = ffi.new_handle(self)
        self._queue = Queue()
        self._worker = None
        self.executor = None

    async def __aenter__(self):
        if self.executor is None:
            self.executor = lib.Cronet_Executor_CreateWith(
                lib._executor_execute,  # noqa: SLF001
            )
            lib.Cronet_Executor_SetClientContext(self.executor, self._handle)

        self._processing_allowed = True

        if self._worker is None:
            self._worker = asyncio.create_task(self._spawn_worker_thread())

        return self

    async def __aexit__(self, exc_type, *args, **kwargs):
        if exc_type is not None:
            self.shutdown()

        try:
            await self._worker
        finally:
            lib.Cronet_Executor_Destroy(self.executor)
            self.executor = None

            self._worker = None

    async def _spawn_worker_thread(self):
        with ThreadPoolExecutor() as pool:
            await asyncio.get_running_loop().run_in_executor(
                pool,
                self._worker_loop,
            )

    def _worker_loop(self):
        while (runnable := self._queue.get()) is not None:
            try:
                if self._processing_allowed:
                    lib.Cronet_Runnable_Run(runnable)
            finally:
                lib.Cronet_Runnable_Destroy(runnable)

    def enqueue_runnable(self, runnable):
        self._queue.put_nowait(runnable)

    def shutdown(self):
        self._processing_allowed = False
        self._queue.put_nowait(None)
