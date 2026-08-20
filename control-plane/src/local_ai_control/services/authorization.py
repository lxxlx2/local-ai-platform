from local_ai_control.domain.identity import IdentityContext, Role


class AuthorizationDenied(PermissionError):
    pass


OWNER_ONLY = {"owner:approvals", "owner:system", "owner:features", "private:projects", "private:tasks", "private:memory", "guidengji:status"}


def require_owner(identity: IdentityContext) -> None:
    if identity.role is not Role.OWNER:
        raise AuthorizationDenied("当前账号没有此操作权限。")


def authorize(identity: IdentityContext, capability: str) -> None:
    if capability in OWNER_ONLY or capability.startswith(("owner:", "private:", "guidengji:")):
        require_owner(identity)


def ensure_owned(identity: IdentityContext, owner_internal_user_id: str) -> None:
    if identity.internal_user_id != owner_internal_user_id:
        raise AuthorizationDenied("当前账号没有此操作权限。")
