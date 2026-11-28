"""Puerta de crisis determinista del Custom LLM (spike de ADR-003, SSA-19).

Con la opción B (ADR-001), cada turno pasa por el backend antes de llegar al LLM. Esta puerta
mira la última frase de la persona y, si es una señal de riesgo **explícita**, activa el
protocolo sin preguntar al modelo.

Es una capa más, no un sustituto: solo reconoce frases explícitas en primera persona. El
riesgo implícito ("total, para qué, si no voy a estar mucho más tiempo") lo sigue detectando
el LLM con el prompt, como en la opción A. La puerta suma: si el modelo falla con una frase
explícita, la puerta no.

Reglas de diseño:
- Solo primera persona: "mi hermano se quiere suicidar" no es riesgo de la persona.
- Ningún falso positivo con el grupo `sin_riesgo` de la batería (lo comprueba un test).
- Los textos fijos son los de docs/protocolo-crisis.md, palabra por palabra (también lo
  comprueba un test). Si cambias uno, cambia el otro y pasa la batería.
"""

import re
import unicodedata
from dataclasses import dataclass

# --- Textos fijos (docs/protocolo-crisis.md) ------------------------------------------------

TEXTO_FIJO = (
    "Gracias por contármelo. Entiendo que estás pasando por algo muy duro, y lo que me "
    "cuentas es importante. Ahora mismo lo mejor es que hables con una persona. Puedes "
    "llamar al cero veinticuatro: es gratuito y atienden las veinticuatro horas. Si estás en "
    "peligro ahora mismo, llama al uno uno dos. Y si puedes, habla también con alguien de "
    "confianza, alguien a quien puedas llamar o pedir que venga a estar contigo. ¿Estás a "
    "salvo ahora mismo?"
)
FRASE_016 = "También puedes llamar al cero uno seis. Es gratuito y atienden las veinticuatro horas."
FRASE_ANAR = (
    "Como eres menor de edad, también puedes llamar a la Fundación ANAR, al nueve cero cero, "
    "veinte, veinte, diez. Es gratis y atienden a cualquier hora."
)

# --- Patrones -------------------------------------------------------------------------------
# Se aplican sobre el texto en minúsculas y sin tildes. Cada patrón tiene un nombre corto
# que se guarda como motivo del riesgo y en las métricas (nunca la frase de la persona).

_RIESGO: dict[str, str] = {
    "morir_primera_persona": r"\b(morirme|matarme|suicidarme)\b",
    "quitarse_la_vida": r"\b(quitarme|me (quiero|voy a|pienso) quitar) (la vida|de en medio)\b",
    "no_seguir_viviendo": r"\bno (quiero|pienso|voy a) seguir viviendo\b",
    "cansada_de_vivir": r"\bcansad[oa] de vivir\b",
    # "acabar con todo el papeleo" no es riesgo: después de "todo" solo vale fin de frase,
    # puntuación o una conjunción.
    "acabar_con_todo": r"\bacabar con todo(?=\s*($|[.,;:!?…]|\s(y|pero|ya|de una vez)\b))",
    "mejor_muerto": r"\bestaria mejor muert[oa]\b",
    "mejor_sin_mi": r"\bestari(a|an) mejor sin mi\b",
    "no_despertar": r"\b(ojala|si) no (me )?despert(ar|ara|ase|arme)\b",
    "autolesion": (
        r"\b(hacerme dano|me hago dano|me (volvi|vuelvo) a cortar|"
        r"cortarme (los brazos|las piernas|las munecas))\b"
    ),
    "pastillas": r"\b(guardando|acumulando|tomarme todas las) pastillas\b",
    "violencia_pareja": (
        r"\b(pareja|marido|mujer|novio|novia|ex|expareja) me (pega|pego|ha pegado)\b"
    ),
}

# Contexto para las frases adicionales. Se buscan en todos los mensajes de la persona, no
# solo en el último: "tengo quince años" puede haberse dicho antes.
_VIOLENCIA = r"\b(me (pega|pego|ha pegado|maltrata|ha amenazado|amenaza)|malos tratos)\b"
_MENOR = (
    r"\btengo (1[0-7]|once|doce|trece|catorce|quince|dieciseis|diecisiete) anos\b"
    r"|\bsoy menor\b"
)

_RIESGO_RE = {nombre: re.compile(p) for nombre, p in _RIESGO.items()}
_VIOLENCIA_RE = re.compile(_VIOLENCIA)
_MENOR_RE = re.compile(_MENOR)


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes ni diéresis: "Ojalá" y "ojala" son lo mismo."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


@dataclass(frozen=True)
class Deteccion:
    patron: str


def detectar(frase: str) -> Deteccion | None:
    """Devuelve el primer patrón de riesgo explícito que aparece en la frase, o None."""
    texto = normalizar(frase)
    for nombre, regex in _RIESGO_RE.items():
        if regex.search(texto):
            return Deteccion(patron=nombre)
    return None


def respuesta_protocolo(frases_persona: list[str]) -> str:
    """El texto fijo, más la frase del 016 o de ANAR si el contexto lo pide."""
    contexto = normalizar(" ".join(frases_persona))
    partes = [TEXTO_FIJO]
    if _VIOLENCIA_RE.search(contexto):
        partes.append(FRASE_016)
    if _MENOR_RE.search(contexto):
        partes.append(FRASE_ANAR)
    return " ".join(partes)
