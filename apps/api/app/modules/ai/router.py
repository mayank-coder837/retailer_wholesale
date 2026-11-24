"""The assistant's endpoints (M1.13): ``ai_assistant.view`` to read, ``ai_assistant.create`` to ask. A tool
then needs the permission of what it does, so the assistant never lets anyone do more than the screens."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.core.ids import new_id
from app.modules.ai import service
from app.modules.ai.planner import Planner, RuleBasedPlanner
from app.modules.ai.registry import ToolContext
from app.modules.ai.schemas import (
    AiActionOut,
    AiCallIn,
    AiChatIn,
    AiChatOut,
    AiDecideIn,
    AiToolOut,
)
from app.modules.identity.authz import get_permissions

router = APIRouter(prefix="/ai", tags=["ai"])
Signed = Annotated[PermissionSet, Depends(get_permissions)]


def get_planner(request: Request) -> Planner:
    """The rules, unless the app installs a model-backed planner (provider not chosen yet: question T3)."""
    planner: Planner | None = getattr(request.app.state, "ai_planner", None)
    return planner or RuleBasedPlanner()


PlannerDep = Annotated[Planner, Depends(get_planner)]


def _ctx(
    session: TenantSessionDep, principal: PrincipalDep, perms: PermissionSet, business_id: uuid.UUID
) -> ToolContext:
    return ToolContext(
        session=session,
        tenant_id=principal.tenant_id,
        business_id=business_id,
        user_id=principal.user_id,
        node_id=principal.node_id,
        perms=perms,
        trace_id=new_id(),
    )


@router.get("/tools", operation_id="list_ai_tools")
async def list_ai_tools(perms: Signed) -> list[AiToolOut]:
    perms.require_held("ai_assistant.view")
    return service.list_tools()


@router.post("/chat", operation_id="ai_chat")
async def ai_chat(
    body: AiChatIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep, planner: PlannerDep
) -> AiChatOut:
    perms.require("ai_assistant.create", body.business_id)
    return await service.chat(
        _ctx(session, principal, perms, body.business_id), planner, body.text, body.lang
    )


@router.post("/call", operation_id="ai_call")
async def ai_call(
    body: AiCallIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> AiActionOut:
    perms.require("ai_assistant.create", body.business_id)
    ctx = _ctx(session, principal, perms, body.business_id)
    return service.action_out(await service.run_call(ctx, body.tool, body.args))


@router.get("/actions", operation_id="list_ai_actions")
async def list_ai_actions(
    business_id: uuid.UUID, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> list[AiActionOut]:
    perms.require("ai_assistant.view", business_id)
    ctx = _ctx(session, principal, perms, business_id)
    return [service.action_out(a) for a in await service.list_actions(ctx)]


@router.post("/actions/{action_id}/confirm", operation_id="confirm_ai_action")
async def confirm_ai_action(
    action_id: uuid.UUID, body: AiDecideIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> AiActionOut:
    perms.require("ai_assistant.create", body.business_id)
    ctx = _ctx(session, principal, perms, body.business_id)
    return service.action_out(await service.confirm(ctx, action_id))


@router.post("/actions/{action_id}/reject", operation_id="reject_ai_action")
async def reject_ai_action(
    action_id: uuid.UUID, body: AiDecideIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> AiActionOut:
    perms.require("ai_assistant.create", body.business_id)
    ctx = _ctx(session, principal, perms, body.business_id)
    return service.action_out(await service.reject(ctx, action_id))
