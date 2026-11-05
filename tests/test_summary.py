from app.models.enums import DrugCategory, TrafficLight
from app.services.summary import (
    best_category,
    category_shares,
    compute_traffic_light,
)


def test_best_category_prefers_a_then_b_then_v() -> None:
    assert (
        best_category([{"category": DrugCategory.V}, {"category": DrugCategory.A}])
        is DrugCategory.A
    )
    assert (
        best_category([{"category": DrugCategory.V}, {"category": DrugCategory.B}])
        is DrugCategory.B
    )
    assert best_category([]) is None


def test_category_shares_ignore_non_positive_and_missing_amounts() -> None:
    shares = category_shares(
        [
            {"amount": 80, "best_category": DrugCategory.A},
            {"amount": 20, "best_category": None},
            {"amount": 0, "best_category": DrugCategory.B},
            {"amount": None, "best_category": DrugCategory.V},
        ]
    )
    assert shares["A"] == 80
    assert shares["undetermined"] == 20
    assert shares["total_amount"] == 100


def test_traffic_light_boundary_and_review() -> None:
    assert compute_traffic_light({"A": 80, "undetermined": 0}, False).color is TrafficLight.RED
    assert compute_traffic_light({"A": 80.01, "undetermined": 0}, False).color is TrafficLight.GREEN
    assert compute_traffic_light({"A": 90, "undetermined": 0}, True).color is TrafficLight.YELLOW


def test_traffic_light_undetermined_share() -> None:
    result = compute_traffic_light({"A": 0, "undetermined": 50}, False)
    assert result.color is TrafficLight.UNDETERMINED
