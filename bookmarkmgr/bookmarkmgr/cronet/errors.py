from ._cronet import lib


class Error(Exception):
    def __init__(self, *args, code=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.code = code


class RequestError(Error):
    pass


def _raise_for_error_result(result):
    if not isinstance(result, int):
        raise TypeError(result)

    if result >= lib.Cronet_RESULT_SUCCESS:
        return

    message = f"Error result {result} returned"

    raise Error(
        message,
        code=result,
    )
