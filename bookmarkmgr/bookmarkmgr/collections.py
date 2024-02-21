from collections import UserDict


class DefaultsDict(UserDict):
    def __init__(self, *args, defaults, **kwargs):
        super().__init__(*args, **kwargs)

        self.defaults = defaults

    def __missing__(self, key):
        return self.defaults[key]
