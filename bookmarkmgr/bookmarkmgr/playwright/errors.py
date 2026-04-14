from typing import override


class NotContextManagerError(RuntimeError):
    @override
    def __init__(self, *args: object) -> None:
        super().__init__(
            "This object must be used as a context manager",
            *args,
        )


class RequestError(Exception):
    pass
