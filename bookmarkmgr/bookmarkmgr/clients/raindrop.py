import asyncio
from collections.abc import AsyncIterator, Awaitable
import math
from typing import Any, cast, TypedDict

from bookmarkmgr.aiohttp import RateLimitedRetryClientSession

from . import ClientSessionContextManagerMixin

RAINDROPS_PER_PAGE = 50


class BaseRaindrop(TypedDict):
    created: str
    link: str
    note: str
    tags: list[str]


class RaindropIn(BaseRaindrop, total=False):
    pass


class RaindropOut(BaseRaindrop):
    _id: int


class CollectionPage(TypedDict):
    count: int
    items: list[RaindropOut]


_Queue = asyncio.Queue[RaindropOut | None]


async def _enqueue_items(
    queue: _Queue,
    items: list[RaindropOut],
) -> None:
    for item in items:
        await queue.put(item)


async def _generator_from_worker_queue(
    queue: _Queue,
    worker: Awaitable[Any],
) -> AsyncIterator[RaindropOut]:
    while (item := await queue.get()) is not None:
        yield item

    await worker


class RaindropClient(ClientSessionContextManagerMixin):
    def __init__(self, api_key: str) -> None:
        self._session = RateLimitedRetryClientSession(
            "https://api.raindrop.io",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            rate_limit=120,
        )

    async def _load_collection_page_items(
        self,
        queue: _Queue,
        collection_id: int,
        page_number: int,
    ) -> None:
        await _enqueue_items(
            queue,
            (
                await self.get_collection_page(
                    collection_id,
                    page_number,
                )
            )["items"],
        )

    async def _load_collection_items(
        self,
        queue: _Queue,
        collection_id: int,
    ) -> None:
        try:
            page = await self.get_collection_page(collection_id, 0)

            await _enqueue_items(queue, page["items"])

            if page["count"] <= RAINDROPS_PER_PAGE:
                return

            async with asyncio.TaskGroup() as task_group:
                for page_number in range(
                    1,
                    math.ceil(page["count"] / RAINDROPS_PER_PAGE),
                ):
                    task_group.create_task(
                        self._load_collection_page_items(
                            queue,
                            collection_id,
                            page_number,
                        ),
                        name=f"Gather-collection-page-{page_number}",
                    )
        finally:
            await queue.put(None)

    async def export_collection(self, collection_id: int) -> str:
        async with self._session.get(
            f"/rest/v1/raindrops/{collection_id}/export.html?sort=-created",
        ) as response:
            return await response.text()

    def get_collection_items(
        self,
        collection_id: int,
    ) -> AsyncIterator[RaindropOut]:
        queue = _Queue()
        worker = asyncio.create_task(
            self._load_collection_items(queue, collection_id),
        )

        return _generator_from_worker_queue(queue, worker)

    async def get_collection_page(
        self,
        collection_id: int,
        page: int,
    ) -> CollectionPage:
        async with self._session.get(
            (
                f"/rest/v1/raindrops/{collection_id}"
                f"?page={page}"
                f"&perpage={RAINDROPS_PER_PAGE}"
            ),
        ) as response:
            return cast(CollectionPage, await response.json())

    async def update_raindrop(
        self,
        raindrop_id: int,
        raindrop: RaindropIn,
    ) -> None:
        await self._session.put(
            f"/rest/v1/raindrop/{raindrop_id}",
            json=raindrop,
        )
