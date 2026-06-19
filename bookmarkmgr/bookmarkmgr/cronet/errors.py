from typing import override, TYPE_CHECKING

from ._cronet import lib

if TYPE_CHECKING:
    from .types import Result


class Error(Exception):
    @override
    def __init__(
        self,
        *args: object,
        code: int | None = None,
    ) -> None:
        super().__init__(*args)

        self.code = code


class NotContextManagerError(RuntimeError):
    @override
    def __init__(self, *args: object) -> None:
        super().__init__(
            "This object must be used as a context manager",
            *args,
        )


class RequestError(Error):
    pass


def _raise_for_error_result(result: Result) -> None:
    if not isinstance(result, int):
        raise TypeError(result)

    if result >= lib.Cronet_RESULT_SUCCESS:
        return

    message = f"Error result {result} returned"

    raise Error(
        message,
        code=result,
    )
