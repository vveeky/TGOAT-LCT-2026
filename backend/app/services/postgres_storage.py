"""PostgreSQL operational connection pool, unit of work, and migrations.

SQLite remains the storage implementation for isolated Historical Replay and
feature-state. Normal operational mode must explicitly configure PostgreSQL.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from psycopg_pool import ConnectionPool


class PostgreSQLConfigurationError(RuntimeError):
    pass


class PostgreSQLMigrationError(RuntimeError):
    pass


class OperationalUnitOfWork:
    """One connection and one transaction shared by repositories in a command."""

    def __init__(self, connection):
        self.connection = connection

    def lock_object(self, object_id: int) -> None:
        """Serialize first-create and mutation paths even before a Signal row exists."""
        self.connection.execute(
            "INSERT INTO object_workflow_locks(object_id) VALUES (%s) "
            "ON CONFLICT(object_id) DO NOTHING",
            (int(object_id),),
        )
        self.connection.execute(
            "SELECT object_id FROM object_workflow_locks WHERE object_id=%s FOR UPDATE",
            (int(object_id),),
        ).fetchone()


class PostgreSQLDatabase:
    # Shared by every process using this application's operational schema.
    # The two-int form is a stable advisory-lock namespace, not a row lock.
    _MIGRATION_LOCK_NAMESPACE = 1414483535
    _MIGRATION_LOCK_ID = 7

    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 12):
        if not database_url or not database_url.startswith(("postgres://", "postgresql://")):
            raise PostgreSQLConfigurationError("TGOAT_OPERATIONAL_DATABASE_URL must be a PostgreSQL URL")
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"autocommit": False},
        )
        self._active_connection: ContextVar[object | None] = ContextVar(
            f"tgoat_pg_connection_{id(self)}", default=None
        )

    def open(self, *, timeout: float = 10.0) -> None:
        try:
            self.pool.open(wait=True, timeout=timeout)
            with self.pool.connection() as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception as error:
            self.pool.close()
            raise PostgreSQLConfigurationError("PostgreSQL operational database is unavailable") from error

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def unit_of_work(self) -> Iterator[OperationalUnitOfWork]:
        """The whole domain command succeeds or rolls back on this connection."""
        active = self._active_connection.get()
        if active is not None:
            # Preserve one request-level transaction while giving each nested
            # repository command rollback isolation if its caller handles an error.
            with active.transaction():
                yield OperationalUnitOfWork(active)
            return
        with self.pool.connection() as connection:
            token = self._active_connection.set(connection)
            try:
                with connection.transaction():
                    yield OperationalUnitOfWork(connection)
            finally:
                self._active_connection.reset(token)

    @contextmanager
    def connection(self):
        """Reuse the command connection or borrow a read connection."""
        active = self._active_connection.get()
        if active is not None:
            yield active
        else:
            with self.pool.connection() as connection:
                yield connection

    @property
    def in_unit_of_work(self) -> bool:
        return self._active_connection.get() is not None

    def migrate(self, migrations_dir: str | Path | None = None) -> list[int]:
        directory = Path(migrations_dir) if migrations_dir else Path(__file__).parents[2] / "migrations"
        files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
        if not files:
            raise PostgreSQLMigrationError(f"No PostgreSQL migrations found in {directory}")
        applied: list[int] = []
        with self.pool.connection() as connection:
            try:
                with connection.transaction():
                    # Serialize even the first-ever metadata-table creation;
                    # IF NOT EXISTS alone is not race-safe for two starters.
                    connection.execute("SELECT pg_advisory_xact_lock(%s,%s)",
                                       (self._MIGRATION_LOCK_NAMESPACE, self._MIGRATION_LOCK_ID))
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS schema_migrations ("
                        "version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                    )
                    # Competing app instances recheck the committed version
                    # after the first lock holder finishes.
                    for path in files:
                        version = int(path.name.split("_", 1)[0])
                        exists = connection.execute(
                            "SELECT 1 FROM schema_migrations WHERE version=%s", (version,)
                        ).fetchone()
                        if exists:
                            continue
                        sql = path.read_text(encoding="utf-8")
                        # This nested transaction is a savepoint; migration
                        # DDL and its version row still commit with the outer
                        # lock-owning transaction.
                        with connection.transaction():
                            # Unprepared/simple-query execution permits the ordered DDL script.
                            connection.execute(sql, prepare=False)
                            connection.execute(
                                "INSERT INTO schema_migrations(version) VALUES (%s)", (version,)
                            )
                        applied.append(version)
            except Exception as error:
                if isinstance(error, PostgreSQLMigrationError):
                    raise
                raise PostgreSQLMigrationError("PostgreSQL migration transaction failed") from error
        return applied

    def healthcheck(self) -> dict[str, str]:
        with self.pool.connection() as connection:
            version_row = connection.execute("SHOW server_version").fetchone()
            version = version_row[0]
        major = int(str(version).split(".", 1)[0])
        if major < 12:
            raise PostgreSQLConfigurationError("PostgreSQL 12 or newer is required")
        return {"server_version": str(version)}
