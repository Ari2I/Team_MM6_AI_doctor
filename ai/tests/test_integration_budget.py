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


def test_completed_attempt_releases_only_unused_token_reservation():
    from llm_rag.integration_budget import RequestBudget

    budget = RequestBudget(deadline=10.0, max_calls=2, max_tokens=100, clock=lambda: 0.0)
    with budget.attempt(90, usage=lambda: 12) as remaining:
        assert remaining == 10.0
        assert budget.tokens == 90
    assert budget.tokens == 12 and budget.token_accounting == "ACTUAL"
    with budget.attempt(80, usage=lambda: 8):
        assert budget.tokens == 92
    assert budget.calls == 2 and budget.tokens == 20


@pytest.mark.parametrize("usage", [None, -1, True, "3", 2.5])
def test_missing_or_invalid_usage_keeps_full_upper_bound(usage):
    from llm_rag.integration_budget import RequestBudget, BudgetExhausted

    budget = RequestBudget(deadline=10.0, max_calls=2, max_tokens=100, clock=lambda: 0.0)
    with budget.attempt(90, usage=lambda: usage):
        pass
    assert budget.tokens == 90 and budget.token_accounting == "UPPER_BOUND"
    with pytest.raises(BudgetExhausted):
        with budget.attempt(11, usage=lambda: 0):
            pytest.fail("Over-budget call must never start")


def test_failed_attempt_never_releases_reservation_or_reuses_previous_usage():
    from llm_rag.integration_budget import RequestBudget

    budget = RequestBudget(deadline=10.0, max_calls=2, max_tokens=100, clock=lambda: 0.0)
    with pytest.raises(OSError):
        with budget.attempt(90, usage=lambda: 1):
            raise OSError("synthetic transport failure")
    assert budget.calls == 1 and budget.tokens == 90
    assert budget.token_accounting == "UPPER_BOUND"


def test_unmeasured_embedding_reservation_keeps_mixed_total_an_upper_bound():
    from llm_rag.integration_budget import RequestBudget

    budget = RequestBudget(deadline=10.0, max_calls=3, max_tokens=200, clock=lambda: 0.0)
    budget.reserve(40)  # Existing Embeddings hook retains its conservative estimate.
    with budget.attempt(90, usage=lambda: 12):
        pass
    assert budget.tokens == 52 and budget.calls == 2
    assert budget.token_accounting == "UPPER_BOUND"


def test_actual_usage_over_reservation_is_charged_and_stops_later_calls():
    from llm_rag.integration_budget import RequestBudget, BudgetExhausted

    budget = RequestBudget(deadline=10.0, max_calls=3, max_tokens=100, clock=lambda: 0.0)
    with budget.attempt(90, usage=lambda: 110):
        pass
    assert budget.tokens == 110 and budget.exhausted
    with pytest.raises(BudgetExhausted):
        with budget.attempt(0, usage=lambda: 0):
            pytest.fail("Actual overrun must stop later calls")
    assert budget.calls == 1


def test_settled_tokens_do_not_restore_call_or_time_limits():
    from llm_rag.integration_budget import RequestBudget, BudgetExhausted

    now = [0.0]
    budget = RequestBudget(deadline=10.0, max_calls=1, max_tokens=100, clock=lambda: now[0])
    with budget.attempt(90, usage=lambda: 1):
        pass
    with pytest.raises(BudgetExhausted):
        with budget.attempt(90, usage=lambda: 1):
            pytest.fail("Call limit must remain effective")
    timed = RequestBudget(deadline=10.0, max_calls=3, max_tokens=100, clock=lambda: now[0])
    with timed.attempt(90, usage=lambda: 1):
        now[0] = 11.0
    with pytest.raises(BudgetExhausted):
        with timed.attempt(90, usage=lambda: 1):
            pytest.fail("Deadline must remain effective")
