from bookmarkmgr.clients.raindrop import RaindropClient


async def export_collection(
    raindrop_client: RaindropClient,
    collection_id: int,
) -> None:
    print(await raindrop_client.export_collection(collection_id))  # noqa: T201
