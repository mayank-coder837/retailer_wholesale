"""merge uses the real edit time that travels with an entry (review fixes to 0022)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-26 09:00:00.000000

* The time of an edit comes with the entry (``sync_outbox.edited_at``), not from the row's own ``updated_at``,
  which another field's later edit may have moved: a relayed entry must be timed as the edit it is.
* Fields the receiving schema has no column for are skipped (a newer branch may know more columns).
* The head-office lookup of the sender happens once, not per field.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MERGE = r"""
CREATE OR REPLACE FUNCTION sync_merge_update(
    p_tenant uuid, p_table text, p_row uuid, p_new jsonb, p_old jsonb, p_origin uuid, p_control jsonb,
    p_edit_at timestamptz
) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    cur jsonb;
    k text;
    applied jsonb := '{}'::jsonb;
    clashes integer := 0;
    in_at timestamptz := coalesce(p_edit_at, (p_new ->> 'updated_at')::timestamptz);
    local_at timestamptz;
    local_node uuid;
    is_control boolean;
    head_in boolean := EXISTS (SELECT 1 FROM node WHERE id = p_origin AND kind = 'cloud');
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
        IF NOT jsonb_exists(cur, k) THEN
            CONTINUE;  -- a column this version does not have
        END IF;
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
    op.execute("DROP FUNCTION sync_merge_update(uuid, text, uuid, jsonb, jsonb, uuid, jsonb)")
    op.execute(MERGE)


def downgrade() -> None:
    # The function is simply absent until 0022 is upgraded again (0022 recreates its own version).
    op.execute("DROP FUNCTION sync_merge_update(uuid, text, uuid, jsonb, jsonb, uuid, jsonb, timestamptz)")
