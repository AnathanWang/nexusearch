"""CostBudget tests: charging, limits, thread safety."""

import threading

import pytest

from nexusearch.ops import CostBudget


def test_charge_until_exhausted():
    budget = CostBudget(max_credits=3)
    assert budget.charge() is True
    assert budget.charge() is True
    assert budget.charge() is True
    assert budget.charge() is False
    assert budget.spent == 3
    assert budget.remaining == 0.0


def test_failed_charge_does_not_spend():
    budget = CostBudget(max_credits=2)
    assert budget.charge(5) is False
    assert budget.spent == 0
    assert budget.remaining == 2


def test_partial_credits():
    budget = CostBudget(max_credits=2.5)
    assert budget.charge(1.5) is True
    assert budget.charge(1.5) is False
    assert budget.charge(1.0) is True
    assert budget.remaining == 0.0


def test_thread_safety_no_overspend():
    budget = CostBudget(max_credits=10)
    results: list[bool] = []

    def worker():
        results.append(budget.charge())

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 10
    assert budget.spent == 10


def test_validation():
    with pytest.raises(ValueError):
        CostBudget(max_credits=0)
    with pytest.raises(ValueError):
        CostBudget(max_credits=-1)
    budget = CostBudget(max_credits=1)
    with pytest.raises(ValueError):
        budget.charge(0)
