"""Migration helpers: one call turns a table into a tenant table (ADR 0002, ADR 0010).

``tenant_table(op, "branch")`` enables + forces row-level security with the tenant policy, grants the app
role, adds the ``touch_row`` trigger (updated_at / updated_by) and the ``sync_outbox_capture`` trigger.
Identifiers interpolated into SQL here are table names from migrations and the role name, which Settings
validates as a plain identifier.
The functions those triggers call are created once by ``create_platform_functions``.
"""

from typing import Protocol

from app.core.config import get_settings

TENANT_POLICY = "tenant_isolation"
CURRENT_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"


class _Op(Protocol):
    def execute(self, sqltext: str) -> object: ...


def app_role() -> str:
    return get_settings().app_db_role


def create_app_role(op: _Op) -> None:
    role = app_role()
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN NOBYPASSRLS;
            END IF;
        END $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT {role} TO CURRENT_USER")


def create_platform_functions(op: _Op) -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION touch_row() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            NEW.updated_by := nullif(current_setting('app.user_id', true), '')::uuid;
            RETURN NEW;
        END $$;
        """
    )
    # TG_ARGV[0] names the column holding the tenant id ('tenant_id', or 'id' for the tenant table itself).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_outbox_capture() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            payload jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
        BEGIN
            INSERT INTO sync_outbox (tenant_id, node_id, table_name, row_id, op, payload)
            VALUES (
                (payload ->> TG_ARGV[0])::uuid,
                nullif(current_setting('app.node_id', true), '')::uuid,
                TG_TABLE_NAME,
                (payload ->> 'id')::uuid,
                lower(TG_OP),
                payload
            );
            RETURN NULL;
        END $$;
        """
    )


def tenant_table(
    op: _Op, table: str, *, tenant_column: str = "tenant_id", sync: bool = True, touch: bool = True
) -> None:
    """``touch=False`` for tables without ``updated_at``/``updated_by``; ``sync=False`` for the outbox."""
    role = app_role()
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {TENANT_POLICY} ON {table} "
        f"USING ({tenant_column} = {CURRENT_TENANT}) WITH CHECK ({tenant_column} = {CURRENT_TENANT})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")
    if touch:
        op.execute(
            f"CREATE TRIGGER touch_row BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION touch_row()"
        )
    if sync:
        op.execute(
            f"CREATE TRIGGER sync_outbox_capture AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION sync_outbox_capture('{tenant_column}')"
        )


def global_table(op: _Op, table: str) -> None:
    """A table without tenant data (users, refresh tokens): no RLS, app role gets DML."""
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role()}")
