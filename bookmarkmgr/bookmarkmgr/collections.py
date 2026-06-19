from collections import UserDict
from collections.abc import Mapping
from typing import cast


class DefaultsDict[KT, VT](UserDict[KT, VT]):
    def __init__(
        self,
        defaults: Mapping[KT, VT],
        data: Mapping[KT, VT] | None = None,
        /,
        **kwargs: Mapping[KT, VT],
    ) -> None:
        super().__init__(data, **kwargs)

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
    data: Data_T  # type: ignore[bad-override-mutable-attribute]
    defaults: Defaults_T  # type: ignore[bad-override-mutable-attribute]

    def __init__(
        self,
        defaults: Defaults_T,
        data: Data_T | None = None,
        /,
        **kwargs: Data_T,
    ) -> None:
        super().__init__(defaults, data, **kwargs)

    def to_typeddict(self) -> Data_T:
        return cast("Data_T", self)
