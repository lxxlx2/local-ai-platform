from dataclasses import dataclass
from enum import IntEnum
from itertools import count
from queue import PriorityQueue

from local_ai_control.domain.identity import Role


class Priority(IntEnum):
    OWNER_INTERACTIVE = 0
    OWNER_IMPORTANT = 1
    OWNER_NORMAL = 2
    PUBLIC_INTERACTIVE = 3
    PUBLIC_MEDIA = 4


@dataclass(frozen=True)
class QueuedJob:
    priority: Priority
    user_id: str
    kind: str


class DeterministicQueue:
    def __init__(self):
        self._items = PriorityQueue()
        self._order = count()

    def put(self, job: QueuedJob):
        self._items.put((int(job.priority), next(self._order), job))

    def get(self) -> QueuedJob:
        return self._items.get_nowait()[2]


def chat_priority(role: Role) -> Priority:
    return Priority.OWNER_INTERACTIVE if role is Role.OWNER else Priority.PUBLIC_INTERACTIVE
