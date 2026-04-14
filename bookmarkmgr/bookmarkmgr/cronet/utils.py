from contextlib import asynccontextmanager, contextmanager
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator


@contextmanager
def destroying[T](obj: T, destroyer: Callable[[T], Any]) -> Iterator[T]:
    try:
        yield obj
    finally:
        destroyer(obj)


@asynccontextmanager
async def adestroying[T](
    obj: T,
    destroyer: Callable[[T], Any],
) -> AsyncIterator[T]:
    with destroying(obj, destroyer) as d:
        yield d
