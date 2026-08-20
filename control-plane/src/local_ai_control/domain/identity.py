from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    OWNER = "OWNER"
    PUBLIC = "PUBLIC"


@dataclass(frozen=True)
class IdentityContext:
    telegram_user_id: str
    internal_user_id: str
    role: Role
    scope: str


def identity_from_telegram(telegram_user_id: int | str, owner_id: str) -> IdentityContext:
    user_id = str(telegram_user_id)
    if user_id == str(owner_id):
        return IdentityContext(user_id, "owner:" + user_id, Role.OWNER, "owner_private")
    return IdentityContext(user_id, "public:" + user_id, Role.PUBLIC, "public_user:" + user_id)
