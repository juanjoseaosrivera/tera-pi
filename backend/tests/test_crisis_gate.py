"""Puerta de crisis determinista (spike de ADR-003).

Los casos salen de la batería del agente (elevenlabs/tests/bateria.json), así que la puerta se
mide con las mismas frases que el LLM. Dos reglas:

- Ningún falso positivo en `sin_riesgo`: "me muero de ganas" no puede activar el protocolo.
- Las frases explícitas en primera persona se detectan todas.

El riesgo implícito no es tarea de la puerta (lo detecta el LLM); el test solo informa de
cuántas frases de la batería cubre.
"""

import json
import re
from pathlib import Path

import pytest

from app.services import crisis_gate

REPO = Path(__file__).resolve().parents[2]
BATERIA = json.loads((REPO / "elevenlabs" / "tests" / "bateria.json").read_text(encoding="utf-8"))
PROTOCOLO = (REPO / "docs" / "protocolo-crisis.md").read_text(encoding="utf-8")

# Tipos de `riesgo` que la persona dice de forma explícita y en primera persona. `peligro` y
# `menor` se dejan fuera: dependen de cómo lo cuente (R26, "estoy en el puente", es implícito).
EXPLICITOS = {"directa", "autolesion", "intento"}


def _frases(grupo: str) -> list[tuple[str, str, str]]:
    return [(c["id"], c.get("tipo", ""), c["frase"]) for c in BATERIA[grupo]]


@pytest.mark.parametrize(("caso", "tipo", "frase"), _frases("sin_riesgo"))
def test_sin_falsos_positivos(caso, tipo, frase):
    assert crisis_gate.detectar(frase) is None, f"{caso}: {frase}"


@pytest.mark.parametrize(
    ("caso", "tipo", "frase"), [c for c in _frases("riesgo") if c[1] in EXPLICITOS]
)
def test_detecta_riesgo_explicito(caso, tipo, frase):
    assert crisis_gate.detectar(frase) is not None, f"{caso}: {frase}"


def test_no_detecta_riesgo_de_otra_persona():
    assert crisis_gate.detectar("Mi hermano dice que se quiere suicidar. ¿Qué hago?") is None


def test_acabar_con_todo_solo_al_final_de_la_frase():
    assert crisis_gate.detectar("Ya no puedo más. Quiero acabar con todo.") is not None
    assert crisis_gate.detectar("Hoy quiero acabar con todo el papeleo") is None


def test_cobertura_de_la_bateria(capsys):
    """Informativo: qué parte de las frases de riesgo cubre la puerta sola."""
    grupos = ("riesgo", "ambiguas", "respuesta")
    for grupo in grupos:
        casos = _frases(grupo)
        detectados = [c for c, _, f in casos if crisis_gate.detectar(f)]
        print(f"{grupo}: {len(detectados)}/{len(casos)} -> {', '.join(detectados)}")
    assert capsys.readouterr().out  # siempre pasa; el resultado se ve con pytest -s


def _sin_formato(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("> ", " ")).strip()


@pytest.mark.parametrize(
    "texto", [crisis_gate.TEXTO_FIJO, crisis_gate.FRASE_016, crisis_gate.FRASE_ANAR]
)
def test_textos_fijos_iguales_que_el_protocolo(texto):
    assert texto in _sin_formato(PROTOCOLO)


def test_respuesta_con_016_y_anar_segun_el_contexto():
    base = crisis_gate.respuesta_protocolo(["Quiero morirme."])
    assert base == crisis_gate.TEXTO_FIJO

    pareja = crisis_gate.respuesta_protocolo(
        ["Mi pareja me pega, y hoy me da miedo volver a casa."]
    )
    assert crisis_gate.FRASE_016 in pareja

    menor = crisis_gate.respuesta_protocolo(["Tengo quince años y a veces quiero morirme."])
    assert crisis_gate.FRASE_ANAR in menor
    assert menor.startswith(crisis_gate.TEXTO_FIJO)
