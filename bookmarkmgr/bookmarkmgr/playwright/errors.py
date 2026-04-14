from typing import Any


class NotContextManagerError(RuntimeError):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(
            "This object must be used as a context manager",
            *args,
            **kwargs,
        )


class RequestError(Exception):
    pass
