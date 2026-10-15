"""Imports every module's models so ``Base.metadata`` is complete (Alembic autogenerate, tests)."""

from app.core import platform_models, sync  # noqa: F401
from app.core.models import Base
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.tenancy import models as tenancy_models  # noqa: F401

metadata = Base.metadata

# Tables without tenant data. Every other table must be a tenant table (RLS + policy + sync trigger +
# isolation test); tests/test_tenant_isolation.py enforces this against the live database.
GLOBAL_TABLES = frozenset({"alembic_version", "user_account", "refresh_token"})
