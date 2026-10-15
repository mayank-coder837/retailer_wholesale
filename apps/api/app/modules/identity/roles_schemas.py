import uuid

from pydantic import BaseModel, EmailStr, Field

from app.modules.identity.schemas import Password


class PermissionOut(BaseModel):
    code: str
    resource: str
    action: str


class RoleIn(BaseModel):
    name_en: str = Field(min_length=1, max_length=100)
    name_ar: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list)


class RolePatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = None


class RoleOut(BaseModel):
    id: uuid.UUID
    system_key: str | None
    name_en: str
    name_ar: str
    description: str | None
    permissions: list[str]
    editable: bool = Field(description="False for the Owner role")


class AssignmentIn(BaseModel):
    role_id: uuid.UUID
    business_id: uuid.UUID | None = Field(default=None, description="Omit for the whole account")
    branch_id: uuid.UUID | None = Field(default=None, description="Requires business_id")


class AssignmentOut(BaseModel):
    role_id: uuid.UUID
    role_name_en: str
    role_name_ar: str
    business_id: uuid.UUID | None
    branch_id: uuid.UUID | None


class AssignmentsIn(BaseModel):
    assignments: list[AssignmentIn]


class MemberIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Password
    assignments: list[AssignmentIn] = Field(min_length=1)


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    status: str
    assignments: list[AssignmentOut]
