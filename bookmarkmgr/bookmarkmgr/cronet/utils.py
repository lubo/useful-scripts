from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator


@contextmanager
def destroying[T](obj: T, destroyer: Callable[[T], object]) -> Generator[T]:
    try:
        yield obj
    finally:
        destroyer(obj)


@asynccontextmanager
async def adestroying[T](
    obj: T,
    destroyer: Callable[[T], object],
) -> AsyncGenerator[T]:
    with destroying(obj, destroyer) as d:
        yield d
