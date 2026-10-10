"""Permission IDs and role grants (06-SECURITY section 4). Single source of truth."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    VIEW = "view"
    ACCOUNT_SELF = "account.self"
    AI_USE = "ai.use"
    ARCHIVES_WRITE = "archives.write"
    RUNS_WRITE = "runs.write"
    NEEDS_INPUT_RESOLVE = "needs_input.resolve"
    RULES_WRITE = "rules.write"
    AI_APPROVE = "ai.approve"
    CLEANUP_MANAGE = "cleanup.manage"
    SETTINGS_MANAGE = "settings.manage"
    SECRETS_MANAGE = "secrets.manage"
    USERS_MANAGE = "users.manage"
    ADMIN_DATA = "admin.data"


_VIEWER = frozenset({Permission.VIEW, Permission.ACCOUNT_SELF, Permission.AI_USE})
_OPERATOR = _VIEWER | {
    Permission.ARCHIVES_WRITE,
    Permission.RUNS_WRITE,
    Permission.NEEDS_INPUT_RESOLVE,
    Permission.RULES_WRITE,
    Permission.AI_APPROVE,
}
_ADMIN = frozenset(Permission)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": _VIEWER,
    "operator": frozenset(_OPERATOR),
    "admin": _ADMIN,
}


def role_has(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
