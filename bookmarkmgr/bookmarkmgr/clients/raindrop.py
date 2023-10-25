import asyncio
import math
from operator import itemgetter

from . import ClientSessionContextManagerMixin
from ..aiohttp import RateLimitedRetryClientSession

RAINDROPS_PER_PAGE = 50


async def _enqueue_items(queue, items):
    for item in items:
        await queue.put(item)


async def _generator_from_worker_queue(queue, worker=None):
    while (item := await queue.get()) is not None:
        yield item

    if worker is not None:
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

    async def export_collection(self, collection_id):
        async with self._session.get(
            f"/rest/v1/raindrops/{collection_id}/export.html?sort=-created",
        ) as response:
            return await response.text()

    async def get_collection_items(self, collection_id):
        queue = asyncio.Queue()

        items, count = itemgetter("items", "count")(
            await self.get_collection_page(collection_id, 0),
        )

        await _enqueue_items(queue, items)

        if count <= RAINDROPS_PER_PAGE:
            await queue.put(None)

            return _generator_from_worker_queue(queue)

        async def load_items(page_number):
            await _enqueue_items(
                queue,
                (
                    await self.get_collection_page(
                        collection_id,
                        page_number,
                    )
                )["items"],
            )

        async def load_the_rest():
            try:
                async with asyncio.TaskGroup() as task_group:
                    for page_number in range(
                        1,
                        math.ceil(count / RAINDROPS_PER_PAGE),
                    ):
                        task_group.create_task(
                            load_items(page_number),
                            name=f"Gather-collection-page-{page_number}",
                        )
            finally:
                await queue.put(None)

        worker = asyncio.create_task(load_the_rest())

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
