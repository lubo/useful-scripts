from collections import UserDict
from collections.abc import Mapping
from typing import Any, cast, TypeVar

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


class TypedDefaultsDict[
    TypedDefaultsDict_Data_T: _TypedDict_T,
    TypedDefaultsDict_Defaults_T: _TypedDict_T,
](
    DefaultsDict[_TypedDict_KT, _TypedDict_VT],
):
    data: TypedDefaultsDict_Data_T  # type: ignore[assignment]
    defaults: TypedDefaultsDict_Defaults_T

    def __init__(
        self,
        *args: Any,
        defaults: TypedDefaultsDict_Defaults_T,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, defaults=defaults, **kwargs)

    def to_typeddict(self) -> TypedDefaultsDict_Data_T:
        return cast("TypedDefaultsDict_Data_T", self)
