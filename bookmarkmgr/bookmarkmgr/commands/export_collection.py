async def export_collection(raindrop_client, collection_id):
    print(await raindrop_client.export_collection(collection_id))  # noqa: T201
