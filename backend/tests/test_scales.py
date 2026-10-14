import pytest

from app.db.models import Scale
from app.services.scales import is_risk_answer, total_score, validate_answer


def test_phq9_total():
    assert total_score(Scale.PHQ9, {i: 1 for i in range(1, 10)}) == 9


def test_incomplete_returns_none():
    assert total_score(Scale.GAD7, {1: 2, 2: 3}) is None


def test_pss10_reverses_positive_items():
    # Todas las respuestas a 0: los ítems 4, 5, 7 y 8 se invierten (0 → 4).
    assert total_score(Scale.PSS10, {i: 0 for i in range(1, 11)}) == 16
    # Todas a 4: solo puntúan los 6 ítems directos.
    assert total_score(Scale.PSS10, {i: 4 for i in range(1, 11)}) == 24


@pytest.mark.parametrize(
    ("scale", "item", "value"),
    [(Scale.PHQ9, 10, 1), (Scale.PHQ9, 1, 4), (Scale.GAD7, 0, 1), (Scale.PSS10, 3, 5)],
)
def test_invalid_answers(scale, item, value):
    with pytest.raises(ValueError):
        validate_answer(scale, item, value)


def test_risk_only_phq9_item9_above_zero():
    assert is_risk_answer(Scale.PHQ9, 9, 1)
    assert not is_risk_answer(Scale.PHQ9, 9, 0)
    assert not is_risk_answer(Scale.PHQ9, 8, 3)
    assert not is_risk_answer(Scale.GAD7, 7, 3)
