from collections import deque
from threading import Lock
from typing import Deque, Dict, List


class RequestHistory:
    def __init__(self, max_items: int = 50):
        self._items: Deque[Dict] = deque(maxlen=max_items)
        self._lock = Lock()

    def add(self, item: Dict) -> None:
        with self._lock:
            self._items.appendleft(item)

    def list(self) -> List[Dict]:
        with self._lock:
            return list(self._items)

    def count(self) -> int:
        with self._lock:
            return len(self._items)


request_history = RequestHistory()
