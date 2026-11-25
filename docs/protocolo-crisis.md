# Protocolo de crisis

> **Estado:** versión 1, pendiente de revisión por un psicólogo colegiado ([SSA-18](https://linear.app/ssapp2/issue/SSA-18)).
> **No cambies el texto fijo sin volver a pasar la batería de riesgo** ([SSA-14](https://linear.app/ssapp2/issue/SSA-14)) y sin cambiarlo a la vez en `elevenlabs/prompt.md`.

## Por qué un texto fijo

Ante una señal de riesgo, el agente no improvisa. Deja la sesión y dice un texto aprobado, siempre igual. Hay tres razones:

1. **Se puede revisar.** Un psicólogo puede aprobar un texto concreto. No puede aprobar "lo que el LLM decida decir".
2. **Se puede probar.** La batería comprueba que aparecen el 024, el 112 y la pregunta de si está a salvo. Con un texto libre, el modelo a veces se deja un recurso.
3. **Es predecible.** En el peor momento de la persona, el agente dice lo mismo que el día que lo probamos.

## Cuándo se activa

Ante cualquiera de estas señales, aunque sea indirecta, en broma o a mitad de otra conversación:

- Ideas de morir, de no querer seguir, de desaparecer, de hacerse daño o de que los demás estarían mejor sin la persona.
- Planes, medios o fechas para hacerse daño, o haberlo intentado.
- Despedidas o frases de que no estará la próxima vez, sin un motivo claro.
- Peligro ahora mismo: violencia, maltrato o alguien que le está haciendo daño.
- Una respuesta distinta de "ningún día" en la pregunta 9 del PHQ-9.
- Una herramienta del backend pide activar el protocolo.

Si la frase es ambigua, el agente pregunta directamente ("¿estás pensando en hacerte daño o en no seguir viviendo?"). Si la respuesta no es un no claro, activa el protocolo. Un falso positivo es mucho menos grave que un falso negativo.

## Qué hace el agente

1. Llama a la herramienta `registrar-riesgo`. **Primero**, antes de hablar.
2. Para la sesión: deja la meditación, las pautas, los cuestionarios y el estoicismo, y no vuelve a ellos.
3. Dice el **texto fijo**, palabra por palabra.
4. Si hay violencia de pareja hacia una mujer, añade la frase del 016.
5. Se queda con la persona: escucha, valida, frases cortas. Si no está a salvo, repite el 112.
6. No cuelga. Si la persona quiere terminar, se despide con la frase fija de despedida.

"Parar la sesión" significa parar el **guion** (meditación, pautas, cuestionarios), no colgar. Cortar la llamada a alguien en crisis lo dejaría solo justo cuando más importa.

## Textos fijos

Los números van escritos en palabras porque los lee una voz sintética: "cero veinticuatro" suena bien; "024" a veces se lee "veinticuatro".

**Texto fijo del protocolo**

> Gracias por contármelo. Entiendo que estás pasando por algo muy duro, y lo que me cuentas es importante. Ahora mismo lo mejor es que hables con una persona. Puedes llamar al cero veinticuatro: es gratuito y atienden las veinticuatro horas. Si estás en peligro ahora mismo, llama al uno uno dos. Y si puedes, habla también con alguien de confianza, alguien a quien puedas llamar o pedir que venga a estar contigo. ¿Estás a salvo ahora mismo?

Cumple los tres requisitos de SSA-12:

| Requisito | Frase |
| --- | --- |
| Decir que el agente entiende | "Entiendo que estás pasando por algo muy duro, y lo que me cuentas es importante." |
| Dar el 024 y el 112 | "Puedes llamar al cero veinticuatro…" / "…llama al uno uno dos." |
| Hablar con una persona de confianza | "Y si puedes, habla también con alguien de confianza…" |

**Frase adicional: violencia de pareja hacia una mujer**

> También puedes llamar al cero uno seis. Es gratuito y atienden las veinticuatro horas.

**Despedida fija**

> Cuídate mucho. Recuerda: cero veinticuatro a cualquier hora, y uno uno dos si estás en peligro.

**Menores de edad** (ya estaba en el prompt): Tera es solo para adultos; se le remite a la Fundación ANAR (900 20 20 10) y, si hay riesgo, además se aplica el protocolo.

### Recursos

| Teléfono | Para qué |
| --- | --- |
| 024 | Línea de atención a la conducta suicida del Ministerio de Sanidad. Gratuita, 24 horas. |
| 112 | Emergencias. Peligro inmediato. |
| 016 | Atención a víctimas de violencia de género. Gratuito, 24 horas. |
| 900 20 20 10 | Fundación ANAR, menores de edad. |

El texto no dice que las llamadas sean "confidenciales" ni que "no avisan a nadie": son condiciones que puede cambiar cada servicio y no las controlamos.

## Las tres capas de seguridad

Una sola capa puede fallar. Por eso hay tres, y cada una cubre los fallos de la anterior:

| Capa | Dónde | Qué cubre |
| --- | --- | --- |
| 1. Regla del prompt | `elevenlabs/prompt.md`, sección "Seguridad" | El agente para la sesión, llama a la tool y dice el texto fijo. Es la que actúa durante la llamada. |
| 2. Tool `registrar-riesgo` | `elevenlabs/tools.json` → `POST /tools/registrar-riesgo` | Marca `risk_flag` en la sesión y crea un `risk_event` con `source = tool`. Su respuesta le recuerda al agente que diga el texto fijo. |
| 3. Criterio post-llamada `protocolo_crisis` | `elevenlabs/agent.json`, `evaluation.criteria` | Al colgar, ElevenLabs revisa la conversación entera y marca si el protocolo se cumplió. Detecta los casos en que el agente falló. |

Además, el dato `riesgo_detectado` (`data_collection`) hace que el webhook cree un `risk_event` con `source = post_call` aunque el agente no llamara a la tool. Y el backend crea uno con `source = phq9_item9` si la pregunta 9 del PHQ-9 no es 0.

## Sesiones siguientes

Al empezar cada sesión, el backend calcula la variable dinámica `{{riesgo_previo}}` (`backend/app/services/memory.py`, `previous_risk`). Se decide en código y no a partir del resumen, porque el resumen lo escribe un LLM y puede no mencionar el riesgo.

| Valor | Cuándo |
| --- | --- |
| `sí, en la última sesión` | La última sesión con conversación tuvo `risk_flag`. Las sesiones que se crearon y nunca llegaron a hablar no cuentan. |
| `sí, en las últimas semanas` | Alguna sesión de los últimos `MEMORY_RISK_DAYS` días (30 por defecto) tuvo `risk_flag`. |
| `no` | Ninguna de las anteriores. |

Qué hace el agente:

- **`sí, en la última sesión`:** cuando la persona contesta al saludo, y antes del ánimo del 1 al 10, dice: "Me alegra que hayas vuelto. La última vez hablamos de algo importante. ¿Cómo estás hoy? ¿Estás a salvo?".
  - Si hay cualquier señal de riesgo, protocolo de crisis.
  - Si está a salvo: "Me alegro. Recuerda que el cero veinticuatro está ahí a cualquier hora". Le pregunta si ha podido hablar con alguien de confianza o con un profesional. Después, sesión normal.
- **`sí, en la última sesión` o `sí, en las últimas semanas`:** sin estoicismo, más escucha que guion, anima a apoyarse en su entorno y en un profesional, y termina con la despedida fija.
- **`no`:** sesión normal.

El agente no sabe qué pasó en la sesión del riesgo (el motivo está cifrado en `risk_events` y no se le pasa). No lo supone, no lo cuenta y no dice que quedó registrado.

## Orden de despliegue

El prompt usa `{{riesgo_previo}}`. Si el agente se despliega antes que el backend, el backend antiguo no envía esa variable y las sesiones reales pueden fallar al empezar. Por eso:

1. Desplegar el backend en la VPS (`deploy/deploy.sh`).
2. Comprobar que `POST /api/sesiones` devuelve `riesgo_previo`.
3. Desplegar el agente: `uv run python ..\elevenlabs\deploy_agent.py`.
4. Pasar la batería de riesgo (SSA-14).

## Pendiente de revisar con el psicólogo (SSA-18)

- El texto fijo, la despedida y la frase de las sesiones siguientes.
- Si en las sesiones después de un riesgo se deben proponer el PHQ-9 y el GAD-7, o esperar.
- Si 30 días es un buen plazo para `sí, en las últimas semanas`.
- Si el agente debe ofrecer el 016 también a hombres víctimas de violencia, o qué recurso dar.
