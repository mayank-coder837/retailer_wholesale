import uuid

import pytest

from app.core.authz import PermissionDeniedError, PermissionSet, Scope
from app.core.permissions import ALL_PERMISSIONS, VOUCHERS, expand
from app.modules.identity.default_roles import default_roles

BIZ, OTHER_BIZ = uuid.uuid4(), uuid.uuid4()
BRANCH_A, BRANCH_B = uuid.uuid4(), uuid.uuid4()


def roles() -> dict[str, frozenset[str]]:
    return {r.system_key: r.permissions for r in default_roles()}


def test_tenant_wide_grant_covers_every_level() -> None:
    perms = PermissionSet.from_grants([("branch.view", Scope())])

    assert perms.allows("branch.view")
    assert perms.allows("branch.view", BIZ)
    assert perms.allows("branch.view", BIZ, BRANCH_A)


def test_business_grant_covers_its_branches_but_not_the_tenant_or_other_businesses() -> None:
    perms = PermissionSet.from_grants([("branch.view", Scope(BIZ))])

    assert perms.allows("branch.view", BIZ)
    assert perms.allows("branch.view", BIZ, BRANCH_B)
    assert not perms.allows("branch.view")
    assert not perms.allows("branch.view", OTHER_BIZ, BRANCH_A)


def test_branch_grant_covers_only_that_branch() -> None:
    perms = PermissionSet.from_grants([("warehouse.view", Scope(BIZ, BRANCH_A))])

    assert perms.allows("warehouse.view", BIZ, BRANCH_A)
    assert not perms.allows("warehouse.view", BIZ, BRANCH_B)
    assert not perms.allows("warehouse.view", BIZ), "business-level actions need business scope"
    assert perms.can_see("warehouse.view", BIZ), "but the branch user can see their business"
    assert not perms.can_see("warehouse.view", OTHER_BIZ)


def test_require_raises_permission_denied_with_the_code() -> None:
    perms = PermissionSet.from_grants([("product.view", Scope())])

    with pytest.raises(PermissionDeniedError) as exc:
        perms.require("product.edit")
    assert exc.value.extra == {"permission": "product.edit"}


def test_expand_patterns_and_reject_typos() -> None:
    assert expand(["sales_invoice.*"]) == {c for c in ALL_PERMISSIONS if c.startswith("sales_invoice.")}
    assert expand(["*"], exclude=["role.*"]) == ALL_PERMISSIONS - {
        c for c in ALL_PERMISSIONS if c.startswith("role.")
    }
    with pytest.raises(ValueError, match="matches nothing"):
        expand(["sales_invoce.*"])


def test_default_roles_are_the_six_from_the_roadmap() -> None:
    assert set(roles()) == {"owner", "manager", "salesperson", "warehouse", "accountant", "cashier"}


def test_owner_has_everything_and_manager_cannot_manage_roles() -> None:
    r = roles()
    assert r["owner"] == ALL_PERMISSIONS
    assert not {"role.create", "role.edit", "role.delete", "period.override_lock"} & r["manager"]
    assert "sales_invoice.post" in r["manager"]


def test_salesperson_cannot_post_vouchers() -> None:
    """Roadmap acceptance (registry level): no voucher can be posted by the default Salesperson."""
    sales = roles()["salesperson"]
    assert not {f"{voucher}.post" for voucher in VOUCHERS} & sales
    assert "sales_invoice.post" in sales


def test_only_owner_and_manager_change_users_and_only_owner_changes_roles() -> None:
    r = roles()
    assert {k for k, p in r.items() if "membership.edit" in p} == {"owner", "manager"}
    assert {k for k, p in r.items() if "role.edit" in p} == {"owner"}
