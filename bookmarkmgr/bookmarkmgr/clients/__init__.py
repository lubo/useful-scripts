class ClientSessionContextManagerMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def __aenter__(self):
        await self._session.__aenter__()

        return self

    async def __aexit__(self, *args, **kwargs):
        await self._session.__aexit__(*args, **kwargs)
