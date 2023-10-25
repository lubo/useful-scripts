from contextlib import asynccontextmanager, contextmanager


@contextmanager
def destroying(obj, destroyer):
    try:
        yield obj
    finally:
        destroyer(obj)


@asynccontextmanager
async def adestroying(obj, destroyer):
    with destroying(obj, destroyer) as d:
        yield d
