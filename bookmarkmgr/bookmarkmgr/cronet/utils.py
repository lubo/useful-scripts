from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, TypeVar

_T = TypeVar("_T")


@contextmanager
def destroying(obj: _T, destroyer: Callable[[_T], Any]) -> Iterator[_T]:
    try:
        yield obj
    finally:
        destroyer(obj)


@asynccontextmanager
async def adestroying(
    obj: _T,
    destroyer: Callable[[_T], Any],
) -> AsyncIterator[_T]:
    with destroying(obj, destroyer) as d:
        yield d
