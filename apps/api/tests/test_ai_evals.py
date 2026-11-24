"""The evaluation set (M1.13): sentences in English and Arabic → the tool calls the rule planner must make.

No network and no model: this keeps the sentence → tool step honest in CI. When a language-model
planner is added (question T3) it is held to the same file.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.ai.planner import LlmPlanner, PlannerUnavailableError, RuleBasedPlanner
from app.modules.ai.registry import REGISTRY

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "ai_evals" / "cases.json").read_text("utf-8")
)


def test_the_set_is_big_enough_and_covers_both_languages() -> None:
    assert len(CASES) >= 30
    assert {c["lang"] for c in CASES} == {"en", "ar"}
    assert sum(1 for c in CASES if c["lang"] == "ar") >= 10
    tools = {c["expect"]["tool"] for c in CASES if "tool" in c["expect"]}
    assert tools == {
        "create_draft_receipt",
        "create_draft_payment",
        "get_receivables_ageing",
        "get_payables_ageing",
        "get_open_bills",
        "list_industry_packs",
    }
    assert any("problem" in c["expect"] for c in CASES)  # and what it must refuse to understand


@pytest.mark.parametrize("case", CASES, ids=[c["text"][:50] for c in CASES])
async def test_the_planner_makes_the_expected_call(case: dict[str, Any]) -> None:
    plan = await RuleBasedPlanner().plan(case["text"], case["lang"])
    expect = case["expect"]
    if "problem" in expect:
        assert plan.calls == []
        assert plan.problem == expect["problem"]
        return
    assert plan.problem is None
    assert len(plan.calls) == 1
    call = plan.calls[0]
    assert call.tool == expect["tool"]
    assert call.tool in REGISTRY
    assert call.party_name == expect.get("party_name")
    if "party_kind" in expect:
        assert call.party_kind == expect["party_kind"]
    assert call.args == expect.get("args", {})
    # what the planner proposes is something the tool's own input model can take (the party id is added later)
    tool = REGISTRY[call.tool]
    needs_party = "party_id" in tool.input.model_fields
    args = {**call.args, **({"party_id": "00000000-0000-0000-0000-000000000001"} if needs_party else {})}
    tool.input.model_validate(args)


async def test_the_model_planner_says_it_is_not_configured() -> None:
    with pytest.raises(PlannerUnavailableError):
        await LlmPlanner().plan("Received 100 from Ali", "en")
