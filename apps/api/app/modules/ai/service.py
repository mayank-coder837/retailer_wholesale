"""Running tools for a person, on their permissions, with a full trace (M1.13).

``run_call`` is the one door: it checks the person may do this, validates the arguments, and either runs a
read or draft tool at once or parks a commit tool as a pending action. ``confirm`` runs a pending action,
but only for the same person and only if they hold the permission *now*. Whatever a tool writes is audited
``via_ai`` on behalf of that person with the trace id; a tool that refuses leaves nothing behind (its writes
sit in a savepoint)."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.core import audit
from app.core.audit import AuditContext
from app.core.errors import DomainError, NotFoundError
from app.core.ids import new_id
from app.modules.ai import tools as _tools  # noqa: F401  (registers the tools)
from app.modules.ai.models import ActionStatus, AiAction
from app.modules.ai.planner import Plan, Planner, PlannerUnavailableError
from app.modules.ai.registry import REGISTRY, Risk, Tool, ToolContext, ToolError
from app.modules.ai.schemas import AiActionOut, AiChatOut, AiToolOut


class UnknownToolError(NotFoundError):
    code = "ai_unknown_tool"
    title = "The assistant has no such tool"


class ActionNotFoundError(NotFoundError):
    code = "ai_action_not_found"
    title = "No such assistant action"


class NotYourActionError(DomainError):
    code = "ai_not_your_action"
    status = HTTPStatus.FORBIDDEN
    title = "Only the person the assistant acted for can decide this"


class ActionNotPendingError(DomainError):
    code = "ai_action_not_pending"
    status = HTTPStatus.CONFLICT
    title = "This action is no longer waiting for a decision"


def tool_out(t: Tool) -> AiToolOut:
    return AiToolOut(
        name=t.name,
        description=t.description,
        risk=t.risk.value,
        input_schema=t.input.model_json_schema(),
    )


def list_tools() -> list[AiToolOut]:
    return [tool_out(t) for t in sorted(REGISTRY.values(), key=lambda t: t.name)]


async def _record(
    ctx: ToolContext,
    tool: Tool,
    status: ActionStatus,
    prompt: str | None,
    args: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> AiAction:
    row = AiAction(
        id=new_id(),
        tenant_id=ctx.tenant_id,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        trace_id=ctx.trace_id,
        tool=tool.name,
        risk=tool.risk.value,
        status=status.value,
        prompt=prompt,
        args=args,
        result=result,
        error_code=error_code,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    return row


def _as_ai(ctx: ToolContext) -> AuditContext | None:
    """Audit what the tool writes as the assistant acting for the person; returns what to put back."""
    before: AuditContext | None = ctx.session.sync_session.info.get(audit.AUDIT_KEY)
    base = before or AuditContext(ctx.user_id, node_id=ctx.node_id)
    audit.set_context(
        ctx.session,
        replace(base, client="ai", via_ai=True, on_behalf_of_user_id=ctx.user_id, ai_trace_id=ctx.trace_id),
    )
    return before


def _restore(ctx: ToolContext, before: AuditContext | None) -> None:
    if before is None:
        ctx.session.sync_session.info.pop(audit.AUDIT_KEY, None)
    else:
        audit.set_context(ctx.session, before)


async def _execute(ctx: ToolContext, tool: Tool, args: BaseModel) -> tuple[bool, dict[str, Any], str | None]:
    """Runs the tool in a savepoint. Returns (worked, result, error code)."""
    before = _as_ai(ctx)
    try:
        async with ctx.session.begin_nested():
            return True, await tool.run(ctx, args), None
    except ToolError as error:
        return False, dict(error.facts), error.code
    except DomainError as error:
        return False, {"detail": error.detail or error.title, **error.extra}, error.code
    finally:
        _restore(ctx, before)


Refusal = tuple[ActionStatus, str, dict[str, Any]]


async def _gate(
    ctx: ToolContext, tool: Tool, raw: dict[str, Any]
) -> tuple[BaseModel | None, dict[str, Any], Refusal | None]:
    """Validates and checks the permission. Returns (args, storable args, a refusal)."""
    try:
        args = tool.input.model_validate(raw)
    except ValidationError as error:
        facts = {"fields": [".".join(str(p) for p in e["loc"]) for e in error.errors()]}
        return None, {}, (ActionStatus.FAILED, "ai_invalid_arguments", facts)
    stored = args.model_dump(mode="json")
    try:
        needed = await tool.needed(ctx, args)
    except DomainError as error:
        return args, stored, (ActionStatus.FAILED, error.code, dict(error.extra))
    if not ctx.perms.allows(needed, ctx.business_id):
        return args, stored, (ActionStatus.DENIED, "permission_denied", {"permission": needed})
    return args, stored, None


async def run_call(ctx: ToolContext, name: str, raw: dict[str, Any], prompt: str | None = None) -> AiAction:
    tool = REGISTRY.get(name)
    if tool is None:
        raise UnknownToolError(tool=name)
    args, stored, refusal = await _gate(ctx, tool, raw)
    if refusal is not None:
        why, reason, facts = refusal
        return await _record(ctx, tool, why, prompt, stored or raw, result=facts, error_code=reason)
    assert args is not None
    if tool.risk is Risk.COMMIT:
        return await _record(ctx, tool, ActionStatus.PENDING, prompt, stored)
    worked, result, code = await _execute(ctx, tool, args)
    status = ActionStatus.DONE if worked else ActionStatus.FAILED
    return await _record(ctx, tool, status, prompt, stored, result=result, error_code=code)


async def _mine(ctx: ToolContext, action_id: uuid.UUID) -> AiAction:
    """The action, locked, if it is this person's in this business."""
    row = (
        await ctx.session.scalars(
            select(AiAction).where(AiAction.id == action_id).with_for_update(),
            execution_options={"populate_existing": True},
        )
    ).first()
    if row is None or row.business_id != ctx.business_id:
        raise ActionNotFoundError()
    if row.user_id != ctx.user_id:
        raise NotYourActionError()
    return row


def _decided(row: AiAction, ctx: ToolContext, status: ActionStatus) -> None:
    row.status = status.value
    row.decided_by = ctx.user_id
    row.decided_at = datetime.now(UTC)


async def confirm(ctx: ToolContext, action_id: uuid.UUID) -> AiAction:
    row = await _mine(ctx, action_id)
    if row.status == ActionStatus.CONFIRMED:
        return row  # a second click does not run it twice
    if row.status != ActionStatus.PENDING:
        raise ActionNotPendingError(status=row.status)
    tool = REGISTRY[row.tool]
    ctx = replace(ctx, trace_id=row.trace_id)
    args, _stored, refusal = await _gate(ctx, tool, row.args)
    if refusal is not None:
        why, reason, facts = refusal
        _decided(row, ctx, why)
        row.error_code = reason
        row.result = facts
        return row
    assert args is not None
    worked, result, code = await _execute(ctx, tool, args)
    _decided(row, ctx, ActionStatus.CONFIRMED if worked else ActionStatus.FAILED)
    row.result = result
    row.error_code = code
    return row


async def reject(ctx: ToolContext, action_id: uuid.UUID) -> AiAction:
    row = await _mine(ctx, action_id)
    if row.status == ActionStatus.REJECTED:
        return row
    if row.status != ActionStatus.PENDING:
        raise ActionNotPendingError(status=row.status)
    _decided(row, ctx, ActionStatus.REJECTED)
    return row


async def list_actions(ctx: ToolContext, *, limit: int = 50) -> list[AiAction]:
    """The person's own recent assistant history in this business, newest first."""
    query = (
        select(AiAction)
        .where(AiAction.business_id == ctx.business_id, AiAction.user_id == ctx.user_id)
        .order_by(AiAction.created_at.desc(), AiAction.id.desc())
        .limit(limit)
    )
    return list(await ctx.session.scalars(query))


def action_out(row: AiAction) -> AiActionOut:
    return AiActionOut.model_validate(row)


def _pick(found: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    if len(found) == 1:
        return found[0]
    wanted = name.strip().lower()
    exact = [p for p in found if wanted in (p["name_en"].lower(), p["name_ar"].lower(), p["code"].lower())]
    return exact[0] if len(exact) == 1 else None


async def chat(ctx: ToolContext, planner: Planner, text: str, lang: str) -> AiChatOut:
    """A sentence in, what was done out. An unclear name becomes a question, never a guess."""
    try:
        plan: Plan = await planner.plan(text, lang)
    except PlannerUnavailableError as error:
        return AiChatOut(trace_id=ctx.trace_id, message=str(error), actions=[])
    actions: list[AiAction] = []
    message: str | None = plan.problem
    facts: dict[str, Any] = {}
    for call in plan.calls:
        args = dict(call.args)
        if call.party_name:
            lookup = await run_call(
                ctx, "find_party", {"name": call.party_name, "kind": call.party_kind}, text
            )
            actions.append(lookup)
            if lookup.status != ActionStatus.DONE.value:
                break
            found = (lookup.result or {}).get("parties", [])
            party = _pick(found, call.party_name)
            if party is None:
                message = "party_not_found" if not found else "party_ambiguous"
                facts = {"name": call.party_name, "matches": found}
                break
            args["party_id"] = party["id"]
            if call.tool == "get_open_bills":
                args["kind"] = call.party_kind
        actions.append(await run_call(ctx, call.tool, args, text))
    return AiChatOut(
        trace_id=ctx.trace_id,
        message=message,
        message_facts=facts,
        actions=[action_out(a) for a in actions],
    )
