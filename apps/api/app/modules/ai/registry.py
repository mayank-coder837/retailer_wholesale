"""The tool registry (M1.13): what the assistant is allowed to call, and how risky each call is.

A tool is one typed function over an existing service. It has a risk tier, the permission it needs (fixed,
or worked out from its input) and a Pydantic input model, which is also the JSON schema a language model is
given. The assistant never passes a business, tenant or user: those come from the signed-in request."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionSet


class Risk(StrEnum):
    READ = "read"  # looks something up: runs at once
    DRAFT = "draft"  # prepares something a person must still post: runs at once
    COMMIT = "commit"  # changes the books for good: only runs when the person confirms


class ToolError(Exception):
    """A tool refuses in words a person can act on; nothing was written. ``code`` is stable."""

    def __init__(self, code: str, **facts: Any) -> None:
        super().__init__(code)
        self.code = code
        self.facts = facts


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    user_id: uuid.UUID
    node_id: uuid.UUID | None
    perms: PermissionSet
    trace_id: uuid.UUID


Run = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
Permission = str | Callable[[ToolContext, Any], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    risk: Risk
    permission: Permission
    input: type[BaseModel]
    run: Run

    async def needed(self, ctx: ToolContext, args: BaseModel) -> str:
        """The permission this call needs (some depend on what the call is about)."""
        return self.permission if isinstance(self.permission, str) else await self.permission(ctx, args)


REGISTRY: dict[str, Tool] = {}


def tool(
    *, name: str, description: str, risk: Risk, permission: Permission, input: type[BaseModel]
) -> Callable[[Run], Run]:
    """Registers a function as a tool."""

    def register(fn: Run) -> Run:
        if name in REGISTRY:
            raise ValueError(f"tool {name} is registered twice")
        REGISTRY[name] = Tool(name, description, risk, permission, input, fn)
        return fn

    return register
