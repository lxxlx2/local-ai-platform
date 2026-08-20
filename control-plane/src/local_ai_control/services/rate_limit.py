from collections import defaultdict, deque
from time import monotonic

from local_ai_control.domain.identity import Role


class PublicRateLimiter:
    """In-memory development limiter. Remote production storage is intentionally pending."""
    def __init__(self, per_minute=12, per_hour=120, per_day=600, clock=monotonic):
        self.limits = ((60, per_minute), (3600, per_hour), (86400, per_day))
        self.clock = clock
        self.events = defaultdict(deque)

    def allow(self, identity):
        if identity.role is Role.OWNER:
            return True
        now = self.clock()
        events = self.events[identity.internal_user_id]
        while events and now - events[0] > 86400:
            events.popleft()
        for seconds, limit in self.limits:
            if sum(now - event <= seconds for event in events) >= limit:
                return False
        events.append(now)
        return True
