from collections import UserDict
from collections.abc import Mapping
from typing import Any, cast


class DefaultsDict[KT, VT](UserDict[KT, VT]):
    def __init__(
        self,
        *args: Any,
        defaults: Mapping[KT, VT],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.defaults = defaults

    def __missing__(self, key: KT) -> VT:
        return self.defaults[key]


_TypedDict_KT = str
_TypedDict_VT = object
_TypedDict_T = Mapping[_TypedDict_KT, _TypedDict_VT]


class TypedDefaultsDict[
    Data_T: _TypedDict_T,
    Defaults_T: _TypedDict_T,
](
    DefaultsDict[_TypedDict_KT, _TypedDict_VT],
):
    data: Data_T  # type: ignore[assignment]
    defaults: Defaults_T  # type: ignore[mutable-override]

    def __init__(
        self,
        *args: Any,
        defaults: Defaults_T,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, defaults=defaults, **kwargs)

    def to_typeddict(self) -> Data_T:
        return cast("Data_T", self)
