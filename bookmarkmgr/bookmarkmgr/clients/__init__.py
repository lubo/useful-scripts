from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, Self


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
        *args: Any,  # noqa: PYI036
        **kwargs: Any,
    ) -> None:
        await self._session.__aexit__(*args, **kwargs)
