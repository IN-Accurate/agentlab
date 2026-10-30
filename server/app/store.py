from copy import deepcopy
from time import monotonic


class CredentialStore:
    def __init__(self):
        # Multiple workers need a shared store in a later deployment.
        self._items = {}

    def put(self, key, value, ttl=7200):
        self._items[str(key)] = (monotonic() + ttl, deepcopy(value))

    def get(self, key):
        item = self._items.get(str(key))
        if item is None:
            return None
        expires, value = item
        if monotonic() >= expires:
            self.delete(key)
            return None
        return deepcopy(value)

    def delete(self, key):
        self._items.pop(str(key), None)


class JobStore(CredentialStore):
    def update(self, key, **values):
        current = self.get(key)
        if current is None:
            return None
        expires, _ = self._items[str(key)]
        current.update(values)
        self._items[str(key)] = (expires, deepcopy(current))
        return deepcopy(current)
