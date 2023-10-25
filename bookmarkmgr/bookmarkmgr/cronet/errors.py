from ._cronet import lib


class Error(Exception):
    def __init__(self, *args, code=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.code = code


class RequestError(Error):
    pass


def _raise_for_error_result(result):
    assert isinstance(result, int)

    if result >= lib.Cronet_RESULT_SUCCESS:
        return

    raise Error(
        f"Error result {result} returned",
        code=result,
    )
