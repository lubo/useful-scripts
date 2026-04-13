from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, Self, TypeVar

_ST = TypeVar("_ST", bound=AbstractAsyncContextManager[Any])


class _HasSession(Protocol[_ST]):
    _session: _ST


class ClientSessionContextManagerMixin(_HasSession[_ST]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    async def __aenter__(self) -> Self:
        await self._session.__aenter__()

        return self

    async def __aexit__(
        self,
        *args: Any,  # noqa: PYI036
        **kwargs: Any,
    ) -> None:
        await self._session.__aexit__(*args, **kwargs)
