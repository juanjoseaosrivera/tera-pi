"""Definición y puntuación de las escalas validadas (ver documento de visión).

Las escalas se usan para medir la evolución, nunca para diagnosticar.
"""

from dataclasses import dataclass

from app.db.models import Scale


@dataclass(frozen=True)
class ScaleSpec:
    items: int
    min_value: int
    max_value: int
    # Ítems que puntúan al revés (en el PSS-10, las preguntas formuladas en positivo).
    reversed_items: frozenset[int] = frozenset()


SCALES: dict[Scale, ScaleSpec] = {
    Scale.PHQ9: ScaleSpec(items=9, min_value=0, max_value=3),
    Scale.GAD7: ScaleSpec(items=7, min_value=0, max_value=3),
    Scale.PSS10: ScaleSpec(
        items=10, min_value=0, max_value=4, reversed_items=frozenset({4, 5, 7, 8})
    ),
}

# Ítem 9 del PHQ-9: pensamientos de que estaría mejor muerto o de hacerse daño.
# Cualquier respuesta distinta de 0 activa el protocolo de crisis.
PHQ9_RISK_ITEM = 9


def validate_answer(scale: Scale, item: int, value: int) -> None:
    spec = SCALES[scale]
    if not 1 <= item <= spec.items:
        raise ValueError(f"{scale} tiene {spec.items} ítems; el ítem {item} no existe")
    if not spec.min_value <= value <= spec.max_value:
        raise ValueError(
            f"En {scale} los valores van de {spec.min_value} a {spec.max_value}; recibido {value}"
        )


def total_score(scale: Scale, answers: dict[int, int]) -> int | None:
    """Puntuación total, o None si la escala aún no está completa."""
    spec = SCALES[scale]
    if set(answers) != set(range(1, spec.items + 1)):
        return None
    total = 0
    for item, value in answers.items():
        if item in spec.reversed_items:
            value = spec.max_value - value
        total += value
    return total


def is_risk_answer(scale: Scale, item: int, value: int) -> bool:
    return scale == Scale.PHQ9 and item == PHQ9_RISK_ITEM and value > 0
