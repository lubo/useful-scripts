import asyncio
import math
from operator import itemgetter

from . import ClientSessionContextManagerMixin
from ..aiohttp import RateLimitedRetryClientSession

RAINDROPS_PER_PAGE = 50


async def _enqueue_items(queue, items):
    for item in items:
        await queue.put(item)


async def _generator_from_worker_queue(queue, worker):
    while (item := await queue.get()) is not None:
        yield item

    await worker


class RaindropClient(ClientSessionContextManagerMixin):
    def __init__(self, api_key):
        self._session = RateLimitedRetryClientSession(
            "https://api.raindrop.io",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            rate_limit=120,
        )

    async def _load_collection_page_items(
        self,
        queue,
        collection_id,
        page_number,
    ):
        await _enqueue_items(
            queue,
            (
                await self.get_collection_page(
                    collection_id,
                    page_number,
                )
            )["items"],
        )

    async def _load_collection_items(self, queue, collection_id):
        try:
            items, count = itemgetter("items", "count")(
                await self.get_collection_page(collection_id, 0),
            )

            await _enqueue_items(queue, items)

            if count <= RAINDROPS_PER_PAGE:
                return

            async with asyncio.TaskGroup() as task_group:
                for page_number in range(
                    1,
                    math.ceil(count / RAINDROPS_PER_PAGE),
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

    async def export_collection(self, collection_id):
        async with self._session.get(
            f"/rest/v1/raindrops/{collection_id}/export.html?sort=-created",
        ) as response:
            return await response.text()

    def get_collection_items(self, collection_id):
        queue = asyncio.Queue()
        worker = asyncio.create_task(
            self._load_collection_items(queue, collection_id),
        )

        return _generator_from_worker_queue(queue, worker)

    async def get_collection_page(self, collection_id, page):
        async with self._session.get(
            (
                f"/rest/v1/raindrops/{collection_id}"
                f"?page={page}"
                f"&perpage={RAINDROPS_PER_PAGE}"
            ),
        ) as response:
            return await response.json()

    async def update_raindrop(self, raindrop_id, raindrop):
        await self._session.put(
            f"/rest/v1/raindrop/{raindrop_id}",
            json=raindrop,
        )
