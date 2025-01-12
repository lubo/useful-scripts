# ruff: noqa: A005

from collections import UserDict
from collections.abc import Mapping
from typing import Any, cast, Generic, TypeVar

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")


class DefaultsDict(UserDict[_KT, _VT]):
    def __init__(
        self,
        *args: Any,
        defaults: Mapping[_KT, _VT],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.defaults = defaults

    def __missing__(self, key: _KT) -> _VT:
        return self.defaults[key]


_TypedDict_KT = str
_TypedDict_VT = object
_TypedDict_T = Mapping[_TypedDict_KT, _TypedDict_VT]

_TypedDefaultsDict_Data_T = TypeVar(
    "_TypedDefaultsDict_Data_T",
    bound=_TypedDict_T,
)
_TypedDefaultsDict_Defaults_T = TypeVar(
    "_TypedDefaultsDict_Defaults_T",
    bound=_TypedDict_T,
)


class TypedDefaultsDict(
    DefaultsDict[_TypedDict_KT, _TypedDict_VT],
    Generic[_TypedDefaultsDict_Data_T, _TypedDefaultsDict_Defaults_T],
):
    data: _TypedDefaultsDict_Data_T  # type: ignore[assignment]
    defaults: _TypedDefaultsDict_Defaults_T

    def __init__(
        self,
        *args: Any,
        defaults: _TypedDefaultsDict_Defaults_T,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, defaults=defaults, **kwargs)

    def to_typeddict(self) -> _TypedDefaultsDict_Data_T:
        return cast(_TypedDefaultsDict_Data_T, self)
