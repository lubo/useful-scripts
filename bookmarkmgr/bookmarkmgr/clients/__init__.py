from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, Self, TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


class _HasSession[ST](Protocol):
    _session: ST


class ClientSessionContextManagerMixin[
    ST: AbstractAsyncContextManager[Any],
](
    _HasSession[ST],
):
    async def __aenter__(self) -> Self:
        await self._session.__aenter__()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._session.__aexit__(exc_type, exc_value, traceback)
