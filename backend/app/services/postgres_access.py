"""Persistent demo access assignments and token revocation."""

from __future__ import annotations

from datetime import datetime, timezone

from psycopg.rows import dict_row

from backend.app.services.auth import PERSONA_GRANTS, ROLE_CAPABILITIES
from backend.app.services.postgres_audit import PostgreSQLAuditRepository


class AccessAssignmentConflict(RuntimeError):
    pass


class InvalidAccessAssignment(ValueError):
    pass


class PostgreSQLAccessRepository:
    ASSIGNABLE_ROLES = {"technician", "district_dispatcher", "central_dispatcher"}

    def __init__(self, database):
        self.database = database

    def token_is_revoked(self, token_id):
        if not token_id:
            return False
        with self.database.connection() as connection:
            return connection.execute(
                "SELECT 1 FROM revoked_auth_tokens WHERE token_id=%s AND expires_at>now()", (token_id,)
            ).fetchone() is not None

    def revoke_token(self, token_id, user_id, expires_at):
        if not token_id:
            return
        with self.database.unit_of_work() as uow:
            uow.connection.execute(
                "INSERT INTO revoked_auth_tokens(token_id,user_id,expires_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (token_id, user_id, expires_at),
            )

    def get_assignment(self, user_id):
        with self.database.connection() as connection:
            row = connection.cursor(row_factory=dict_row).execute(
                "SELECT * FROM access_assignments WHERE user_id=%s", (user_id,)
            ).fetchone()
        return row

    @staticmethod
    def _scope_roots(groups):
        from backend.app.services.reference_data import get_objects_by_id
        objects = get_objects_by_id()
        roots = set()
        for group in groups:
            try:
                kind, identifier = group.split(":", 1)
                object_id = int(identifier)
            except (ValueError, AttributeError) as error:
                raise InvalidAccessAssignment("Groups must be complex:<object_id> or district:<object_id>") from error
            if kind not in {"complex", "district"} or object_id not in objects:
                raise InvalidAccessAssignment("Group references an unknown object scope")
            roots.add(object_id)
        return sorted(roots)

    def _validate(self, roles, grants, groups):
        roles = list(dict.fromkeys(roles))
        grants = list(dict.fromkeys(grants))
        groups = list(dict.fromkeys(groups))
        unknown_roles = set(roles) - self.ASSIGNABLE_ROLES
        unknown_grants = set(grants) - PERSONA_GRANTS
        if unknown_roles or unknown_grants:
            raise InvalidAccessAssignment("Assignment contains a non-assignable role or grant")
        roots = self._scope_roots(groups)
        group_kinds = {group.split(":", 1)[0] for group in groups}
        if "technician" in roles:
            complex_groups = [group for group in groups if group.startswith("complex:")]
            if len(complex_groups) != 1:
                raise InvalidAccessAssignment("Technician access requires exactly one assigned complex group")
        if "district_dispatcher" in roles and "district" not in group_kinds:
            raise InvalidAccessAssignment("District dispatch access requires a district group")
        if grants and not groups and "central_dispatcher" not in roles:
            raise InvalidAccessAssignment("Read-only persona grants require an explicit object scope")
        if "model.explain" in grants and "evidence.read" not in grants:
            grants.append("evidence.read")
        return roles, grants, groups, roots

    def save_assignment(self, *, target, username, roles, grants, groups,
                        expected_version, actor):
        if target == actor.user_id:
            raise InvalidAccessAssignment("Administrators cannot change their own access assignment")
        roles, grants, groups, roots = self._validate(roles, grants, groups)
        with self.database.unit_of_work() as uow:
            connection = uow.connection
            current = connection.cursor(row_factory=dict_row).execute(
                "SELECT * FROM access_assignments WHERE user_id=%s FOR UPDATE", (target,)
            ).fetchone()
            current_version = current["version"] if current else 0
            if current_version != expected_version:
                raise AccessAssignmentConflict("Access assignment version is stale")
            previous = {"roles": list(current["roles"]), "grants": list(current["grants"]),
                        "groups": list(current["groups"]), "scope_roots": list(current["scope_roots"])} if current else None
            next_version = current_version + 1
            row = connection.cursor(row_factory=dict_row).execute(
                """INSERT INTO access_assignments(user_id,roles,grants,groups,scope_roots,version,updated_by_user_id,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(user_id) DO UPDATE SET roles=excluded.roles,grants=excluded.grants,
                     groups=excluded.groups,scope_roots=excluded.scope_roots,version=excluded.version,
                     updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at
                   RETURNING *""",
                (target, roles, grants, groups, roots, next_version, actor.user_id, datetime.now(timezone.utc)),
            ).fetchone()
            PostgreSQLAuditRepository.append_in_connection(connection, {
                "actor_user_id": actor.user_id, "actor_roles": list(actor.roles),
                "actor_scope": {"groups": list(actor.groups), "scope_roots": list(actor.scope_roots)
                                if actor.scope_roots is not None else None},
                "action": "access.assignment.changed", "resource_type": "user_access",
                "resource_id": target, "outcome": "success", "source_mode": "admin_api",
                "previous_state": previous,
                "new_state": {"roles": roles, "grants": grants, "groups": groups, "scope_roots": roots,
                               "version": next_version},
                "metadata": {"target_username": username},
            })
            return row
