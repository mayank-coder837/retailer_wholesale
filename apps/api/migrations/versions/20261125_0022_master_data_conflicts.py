"""master-data conflicts (M2.1c, ADR 0011): old values in the outbox, the merge function, sync_conflict

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-25 10:00:00.000000

* ``sync_outbox.changed``: for an update, each changed column with its value *before* the edit, so a receiver can
  tell "only the sender changed this" from "both of us changed it" (a three-way merge, no per-field clock table).
* ``sync_outbox.edited_at``: when the edit really happened (the sender's time, kept as the row travels), the
  per-field clock: the latest outbox row naming a field is that field's last edit.
* ``touch_row`` leaves ``updated_at`` alone while sync applies a row, so a received row keeps its edit time.
* ``sync_merge_update``: applies an incoming update field by field; same-field clashes are decided by the later
  edit (head office wins on control fields) and logged in ``sync_conflict`` with both values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core import rls

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPTURE = """
CREATE OR REPLACE FUNCTION sync_outbox_capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payload jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
    changed jsonb;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(jsonb_object_agg(o.key, o.value), '{}'::jsonb) INTO changed
        FROM jsonb_each(to_jsonb(OLD)) AS o(key, value)
        JOIN jsonb_each(payload) AS n(key, value) ON n.key = o.key
        WHERE o.value IS DISTINCT FROM n.value AND o.key NOT IN ('updated_at', 'updated_by');
    END IF;
    INSERT INTO sync_outbox (tenant_id, node_id, table_name, row_id, op, payload, changed, edited_at)
    VALUES (
        (payload ->> TG_ARGV[0])::uuid,
        nullif(current_setting('app.node_id', true), '')::uuid,
        TG_TABLE_NAME,
        (payload ->> 'id')::uuid,
        lower(TG_OP),
        payload,
        changed,
        coalesce(nullif(current_setting('app.edit_at', true), '')::timestamptz, now())
    );
    RETURN NULL;
END $$;
"""

OLD_CAPTURE = """
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

TOUCH = """
CREATE OR REPLACE FUNCTION touch_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('app.sync_apply', true) = 'on' THEN
        RETURN NEW;  -- a row that arrived by sync keeps the time of the edit, not of its arrival
    END IF;
    NEW.updated_at := now();
    NEW.updated_by := nullif(current_setting('app.user_id', true), '')::uuid;
    RETURN NEW;
END $$;
"""

OLD_TOUCH = """
CREATE OR REPLACE FUNCTION touch_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    NEW.updated_by := nullif(current_setting('app.user_id', true), '')::uuid;
    RETURN NEW;
END $$;
"""

# p_old: the sender's values before the edit, only for the fields it changed. p_control: field names that head
# office wins on, or ["*"] for the whole table. Returns how many fields clashed.
MERGE = r"""
CREATE OR REPLACE FUNCTION sync_merge_update(
    p_tenant uuid, p_table text, p_row uuid, p_new jsonb, p_old jsonb, p_origin uuid, p_control jsonb
) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    cur jsonb;
    k text;
    applied jsonb := '{}'::jsonb;
    clashes integer := 0;
    in_at timestamptz := (p_new ->> 'updated_at')::timestamptz;
    local_at timestamptz;
    local_node uuid;
    is_control boolean;
    head_in boolean;
    head_local boolean;
    incoming_wins boolean;
    sets text;
BEGIN
    EXECUTE format('SELECT to_jsonb(t) FROM %I t WHERE t.id = $1', p_table) INTO cur USING p_row;
    IF cur IS NULL THEN  -- not here yet: take the whole row
        EXECUTE format(
            'INSERT INTO %1$I SELECT * FROM jsonb_populate_record(NULL::%1$I, $1) ON CONFLICT (id) DO NOTHING',
            p_table
        ) USING p_new;
        RETURN 0;
    END IF;
    FOR k IN SELECT jsonb_object_keys(p_old) LOOP
        IF cur -> k IS NOT DISTINCT FROM p_old -> k THEN
            applied := applied || jsonb_build_object(k, p_new -> k);  -- only the sender changed it
        ELSIF cur -> k IS NOT DISTINCT FROM p_new -> k THEN
            CONTINUE;  -- already the same
        ELSE
            clashes := clashes + 1;
            SELECT o.edited_at, o.node_id INTO local_at, local_node
            FROM sync_outbox o
            WHERE o.tenant_id = p_tenant AND o.table_name = p_table AND o.row_id = p_row
              AND o.op = 'update' AND jsonb_exists(o.changed, k)
            ORDER BY o.id DESC LIMIT 1;
            IF local_at IS NULL THEN
                local_at := (cur ->> 'updated_at')::timestamptz;
            END IF;
            is_control := jsonb_exists(p_control, '*') OR jsonb_exists(p_control, k);
            head_in := EXISTS (SELECT 1 FROM node WHERE id = p_origin AND kind = 'cloud');
            head_local := EXISTS (SELECT 1 FROM node WHERE id = local_node AND kind = 'cloud');
            IF is_control AND head_in <> head_local THEN
                incoming_wins := head_in;
            ELSE
                incoming_wins := (in_at, coalesce(p_origin, '00000000-0000-0000-0000-000000000000'::uuid))
                    > (local_at, coalesce(local_node, '00000000-0000-0000-0000-000000000000'::uuid));
            END IF;
            INSERT INTO sync_conflict (
                id, tenant_id, table_name, row_id, field, kept_value, lost_value,
                kept_node_id, lost_node_id, kept_at, lost_at, reason
            ) VALUES (
                gen_random_uuid(), p_tenant, p_table, p_row, k,
                CASE WHEN incoming_wins THEN p_new -> k ELSE cur -> k END,
                CASE WHEN incoming_wins THEN cur -> k ELSE p_new -> k END,
                CASE WHEN incoming_wins THEN p_origin ELSE local_node END,
                CASE WHEN incoming_wins THEN local_node ELSE p_origin END,
                CASE WHEN incoming_wins THEN in_at ELSE local_at END,
                CASE WHEN incoming_wins THEN local_at ELSE in_at END,
                CASE WHEN is_control AND head_in <> head_local THEN 'head_office' ELSE 'later_edit' END
            );
            IF incoming_wins THEN
                applied := applied || jsonb_build_object(k, p_new -> k);
            END IF;
        END IF;
    END LOOP;
    IF applied = '{}'::jsonb THEN
        RETURN clashes;
    END IF;
    IF in_at > (cur ->> 'updated_at')::timestamptz THEN
        applied := applied || jsonb_build_object('updated_at', p_new -> 'updated_at', 'updated_by', p_new -> 'updated_by');
    END IF;
    SELECT string_agg(format('%1$I = r.%1$I', key), ', ') INTO sets FROM jsonb_object_keys(applied) AS key;
    PERFORM set_config('app.edit_at', in_at::text, true);  -- the outbox row records when the edit really happened
    EXECUTE format(
        'UPDATE %1$I t SET %2$s FROM (SELECT * FROM jsonb_populate_record(NULL::%1$I, $1)) r WHERE t.id = $2',
        p_table, sets
    ) USING applied, p_row;
    PERFORM set_config('app.edit_at', '', true);
    RETURN clashes;
END $$;
"""


def upgrade() -> None:
    op.add_column("sync_outbox", sa.Column("changed", postgresql.JSONB(), nullable=True))
    op.add_column(
        "sync_outbox",
        sa.Column("edited_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_sync_outbox_row", "sync_outbox", ["tenant_id", "table_name", "row_id"])
    op.execute(CAPTURE)
    op.execute(TOUCH)

    op.create_table(
        "sync_conflict",
        sa.Column("table_name", sa.String(length=63), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(length=63), nullable=False),
        sa.Column("kept_value", postgresql.JSONB(), nullable=True),
        sa.Column("lost_value", postgresql.JSONB(), nullable=True),
        sa.Column("kept_node_id", sa.Uuid(), nullable=True),
        sa.Column("lost_node_id", sa.Uuid(), nullable=True),
        sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(),
            server_default=sa.text("nullif(current_setting('app.user_id', true), '')::uuid"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_by",
            sa.Uuid(),
            server_default=sa.text("nullif(current_setting('app.user_id', true), '')::uuid"),
            nullable=True,
        ),
        sa.Column(
            "origin_node_id",
            sa.Uuid(),
            server_default=sa.text("nullif(current_setting('app.node_id', true), '')::uuid"),
            nullable=True,
        ),
        sa.CheckConstraint("reason IN ('later_edit', 'head_office')", name=op.f("ck_sync_conflict_reason")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name=op.f("fk_sync_conflict_tenant_id_tenant")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_conflict")),
    )
    op.create_index(op.f("ix_sync_conflict_tenant_id"), "sync_conflict", ["tenant_id"], unique=False)
    op.create_index("ix_sync_conflict_open", "sync_conflict", ["tenant_id", "created_at"])
    # Never synced: each database logs the clashes it decided (the audit log of the winner travels as usual).
    rls.tenant_table(op, "sync_conflict", sync=False)
    op.execute(MERGE)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS sync_merge_update(uuid, text, uuid, jsonb, jsonb, uuid, jsonb)")
    op.drop_index("ix_sync_conflict_open", table_name="sync_conflict")
    op.drop_index(op.f("ix_sync_conflict_tenant_id"), table_name="sync_conflict")
    op.drop_table("sync_conflict")
    op.execute(OLD_TOUCH)
    op.execute(OLD_CAPTURE)
    op.drop_index("ix_sync_outbox_row", table_name="sync_outbox")
    op.drop_column("sync_outbox", "edited_at")
    op.drop_column("sync_outbox", "changed")
