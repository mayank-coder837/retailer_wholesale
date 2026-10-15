import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.identity.models import ClientKind
from app.modules.tenancy.schemas import BranchIn, BusinessIn

Password = Field(min_length=10, max_length=128)


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Password
    full_name: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="en", pattern=r"^(en|ar)$")
    tenant_name: str = Field(min_length=1, max_length=200, description="Account name, usually the trade name")
    business: BusinessIn
    head_office: BranchIn
    client: ClientKind = ClientKind.WEB


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    tenant_id: uuid.UUID | None = Field(default=None, description="Required when the user belongs to several")
    client: ClientKind = ClientKind.WEB


class RefreshIn(BaseModel):
    refresh_token: str | None = Field(default=None, description="Desktop sends it here; web uses the cookie")


class SwitchTenantIn(BaseModel):
    tenant_id: uuid.UUID
    client: ClientKind = ClientKind.WEB


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    refresh_token: str | None = Field(default=None, description="Only for desktop clients")
    tenant_id: uuid.UUID
    membership_id: uuid.UUID


class MembershipOut(BaseModel):
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    status: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    locale: str


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    default_locale: str


class MeOut(BaseModel):
    user: UserOut
    tenant: TenantOut
    membership_id: uuid.UUID
    node_id: uuid.UUID | None
    memberships: list[MembershipOut]
