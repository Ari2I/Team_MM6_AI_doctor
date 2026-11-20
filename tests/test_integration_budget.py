"""Общий бюджет учитывает фактические попытки и остаток времени."""

import pytest


def test_budget_reserves_tokens_and_limits_calls_before_invocation():
    from llm_rag.integration_budget import RequestBudget, BudgetExhausted

    budget = RequestBudget(deadline=10.0, max_calls=1, max_tokens=20, clock=lambda: 0.0)
    assert budget.reserve(10) == 10.0
    assert budget.calls == 1 and budget.tokens == 10
    with pytest.raises(BudgetExhausted):
        budget.reserve(1)
    assert budget.calls == 1


def test_expired_deadline_never_consumes_call():
    from llm_rag.integration_budget import RequestBudget, BudgetExhausted

    budget = RequestBudget(deadline=1.0, max_calls=10, max_tokens=100, clock=lambda: 2.0)
    with pytest.raises(BudgetExhausted):
        budget.reserve(1)
    assert budget.calls == 0
