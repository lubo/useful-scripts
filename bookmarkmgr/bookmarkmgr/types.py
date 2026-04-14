from typing import NamedTuple


class _BaseResult[T](NamedTuple):
    value: T


class Failure[T](_BaseResult[T]):
    pass


class Success[T](_BaseResult[T]):
    pass


type Result[TS, TF] = Success[TS] | Failure[TF]
