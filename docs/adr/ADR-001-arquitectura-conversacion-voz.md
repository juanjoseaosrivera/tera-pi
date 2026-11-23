# ADR-001: Arquitectura de la conversación por voz (dónde vive el LLM)

- **Estado:** Aceptado. Validado con el spike el 31 de octubre de 2026 (ver [Resultados del spike](#resultados-del-spike))
- **Fecha:** 11 de octubre de 2026
- **Autor:** Juanjo
- **Relacionado con:** [Documento de visión y alcance](../vision-y-alcance.md)

## Contexto

Tera-pi mantiene una conversación por voz en manos libres. En cada turno hay cuatro piezas:

1. **STT** (voz a texto): entiende lo que dice el usuario.
2. **Gestión de turnos**: detecta cuándo ha terminado de hablar y permite interrumpir.
3. **LLM**: decide qué responder.
4. **TTS** (texto a voz): lo convierte en la voz del asistente.

La pregunta es **quién controla el LLM**. De ahí dependen la seguridad (protocolo de crisis), la memoria entre sesiones, el tratamiento de datos de salud, la latencia y la complejidad del proyecto.

**Entorno actual:**

- VPS Hostinger en **París (Francia)**, Ubuntu 24.04 con CloudPanel.
- ElevenLabs con plan **Starter**: 6 conversaciones simultáneas (18 con *bursting*), confirmado por la API el 19 de noviembre de 2026. Según una fuente externa de septiembre de 2026, incluye unos 75 minutos de agente al mes; falta confirmarlo en el panel. No incluye residencia de datos en la UE ni *Zero Retention Mode*, que son de Enterprise.

## Opciones consideradas

### Opción A: todo en ElevenLabs (LLM integrado)

ElevenLabs hace STT, turnos, LLM (eligiendo un modelo en la configuración del agente) y TTS. Nuestro backend interviene en tres puntos:

- **Al empezar:** pasa contexto con variables dinámicas (`{{resumen_previo}}`, `{{pautas}}`…) y entrega al navegador una *signed URL*.
- **Durante la llamada:** el agente llama a nuestras **server tools** (webhooks) para guardar pautas, puntuaciones o avisos de riesgo.
- **Al terminar:** recibe la transcripción y el análisis por el **webhook post-llamada**, firmado con HMAC.

| A favor | En contra |
| --- | --- |
| La menor latencia posible | El backend no ve cada turno: el protocolo de crisis depende de que el modelo siga el prompt |
| Poco código; no hace falta API key de un proveedor de LLM | Dependencia del proveedor |
| Un solo proveedor y una sola factura | Probar el comportamiento automáticamente es más difícil |

### Opción B: LLM propio en nuestro backend (Custom LLM)

ElevenLabs hace la voz y en cada turno llama a un endpoint nuestro compatible con OpenAI. Cada turno pasa por nuestro código, así que permite una puerta de crisis determinista. El coste es más latencia, más código y un backend crítico en el camino de la voz.

### Opción C: pipeline completo propio

Descartada: hay que resolver audio en tiempo real, detección de fin de habla e interrupciones.

## Decisión

**Opción A: todo el proceso de voz y el LLM viven en ElevenLabs.** Nuestro backend FastAPI gestiona sesiones, memoria, server tools y webhooks, pero no está en el camino de cada turno.

Motivos: es la vía más simple para el MVP, no exige un segundo proveedor ni otra API key, tiene la menor latencia y permite aprender la plataforma a fondo.

**Condición para revisar esta decisión:** antes de abrir Tera-pi a usuarios reales fuera del círculo de pruebas, se escribirá un ADR nuevo para valorar el paso a la opción B (Custom LLM), que es la que da una puerta de crisis determinista. El backend se diseña desde ya para que ese paso sea pequeño.

### Cómo funciona una sesión

```mermaid
sequenceDiagram
    participant U as Usuario (Angular)
    participant B as Backend FastAPI (VPS)
    participant E as Agente ElevenLabs (voz + LLM)

    U->>B: POST /api/sesiones (inicio)
    B->>B: Crea sesión, carga resumen previo y pautas
    B->>E: Pide signed URL (API key solo en el backend)
    B-->>U: signed URL + variables dinámicas
    U->>E: Abre conversación (SDK web, micrófono)
    loop Conversación
        E->>E: STT + LLM + TTS
        opt Cuando el agente lo necesita
            E->>B: Server tool (guardar_pauta, registrar_animo, registrar_riesgo…)
            B-->>E: Resultado
        end
    end
    E->>B: Webhook post-llamada (HMAC): transcripción + análisis
    B->>B: Guarda resumen, pautas, ánimo final y alertas
```

### Reglas de diseño

1. **La configuración del agente (prompt, modelo, herramientas, análisis) vive en git** como JSON en `elevenlabs/` y se despliega por la API de ElevenLabs. Nunca se edita a mano en el panel sin reflejarlo en el repositorio.
2. **El navegador nunca ve la API key de ElevenLabs:** agente privado y *signed URL* generada por el backend.
3. **El webhook post-llamada se valida siempre** con la firma `ElevenLabs-Signature` y su marca de tiempo.
4. **Se desactiva el envío y la grabación de audio** y se revisan los ajustes de retención del agente: no guardamos audio (minimización de datos).
5. **El protocolo de crisis en la opción A tiene tres capas**, todas imperfectas, que se combinan:
    - Reglas explícitas en el prompt: ante cualquier señal de riesgo, el agente deja de meditar, responde con el texto del protocolo y da los recursos (024, 112).
    - Herramienta obligatoria `registrar_riesgo`, que el agente debe llamar en ese caso; el backend la registra y alerta.
    - Criterio de evaluación "riesgo detectado" en el análisis post-llamada, para cazar casos que el agente no marcó durante la conversación.
6. **Batería de frases de riesgo:** se prueba contra el agente antes de cada cambio de prompt o de modelo.
7. **El backend no usa frameworks de orquestación de LLM** (LangChain o similares): no hay LLM propio que orquestar.

## Consecuencias

**Positivas**

- MVP más rápido y con menos piezas que mantener.
- La memoria, las herramientas y los datos ya están en nuestro backend, así que pasar a la opción B más adelante solo exige mover el LLM.

**Negativas y cómo las mitigamos**

- **La seguridad depende del modelo.** Se mitiga con las tres capas y la batería de pruebas, y queda como riesgo abierto hasta revisar la decisión antes de tener usuarios reales.
- **ElevenLabs y el proveedor del LLM tratan la conversación.** Se minimiza lo que se guarda en su lado. La residencia en la UE y el *Zero Retention Mode* requieren el plan Enterprise.
- **Dependencia del proveedor.** Se mitiga con la configuración en git y el backend desacoplado.

## Validación (spike)

- [x] Comprobar el datacenter de la VPS: París (Francia).
- [x] Endurecer la VPS antes de exponer endpoints: usuario `juanjoadmin` con sudo, SSH solo con clave, MySQL solo en `127.0.0.1`, firewall reducido y actualizaciones aplicadas ([Infra 02](../infra/02-seguridad-servidor.md)).
- [x] Crear un agente privado de prueba y guardar su configuración en `elevenlabs/` (`agent.json`, `tools.json`, `prompt.md`), desplegada con `deploy_agent.py`.
- [x] Backend mínimo: `POST /api/sesiones` genera la *signed URL* y las variables dinámicas ([Infra 03](../infra/03-despliegue-backend.md)).
- [x] Server tools y webhook post-llamada con validación HMAC. Al final son cinco: `registrar-animo`, `guardar-pauta`, `revisar-pauta`, `registrar-respuesta-escala` y `registrar-riesgo`.
- [ ] **Parcial.** Probar 2–3 modelos del panel. Se compararon dos por latencia y tono (`gemini-3.1-pro-preview` y `gemini-2.5-flash`). **Falta** probar el protocolo de crisis con la batería de frases de riesgo (regla 6).
- [ ] **Parcial.** Confirmar el plan Starter: concurrencia confirmada (6, o 18 con *bursting*); minutos al mes, pendientes de ver en el panel.

## Resultados del spike

Cuatro sesiones de prueba por voz, entre el 25 y el 31 de octubre de 2026, con el agente "Tera-pi (spike)" y el backend en `https://api.tera-pi.pygmalion.cloud`.

### La arquitectura funciona

- **Sesión completa:** check-in, conversación, cuestionarios (PHQ-9 y GAD-7), pautas, respiración guiada, cierre y webhook. Las *server tools* responden en 0,4–0,6 s.
- **Memoria entre sesiones:** el agente recuerda el resumen anterior, pregunta por las pautas pendientes una a una, las marca con `revisar-pauta` y no repite cuestionarios que no tocan.
- **Evaluación de ElevenLabs:** los criterios `protocolo_crisis`, `sin_diagnostico` y `ciclo_sesion` funcionan como red de seguridad y como métrica.

### Modelo: `gemini-2.5-flash`

| Modelo | Resultado |
| --- | --- |
| `gemini-3.1-pro-preview` | En muchos turnos fallaba a los ~4 s y ElevenLabs pasaba al modelo de respaldo: pausas de 5–10 s antes de responder |
| `gemini-2.5-flash` | 0,4–1,4 s hasta empezar a responder; sigue bien el guion y las herramientas |

**Decisión:** `gemini-2.5-flash` como modelo principal. Es provisional hasta pasar la batería de frases de riesgo: si no la supera, se prueban otros modelos antes de cambiar el prompt.

### Coste por sesión (plan Starter)

| Sesión | Duración | Coste aproximado |
| --- | --- | --- |
| Con PHQ-9 y GAD-7 (16 preguntas) | 12 min | 1,30 $ (0,95 $ de voz) |
| Sin cuestionarios | 6 min | 0,48 $ (0,45 $ de voz) |

Casi todo el coste es la voz: el LLM supone unos 0,03 $ por sesión. Los cuestionarios ocupan la mitad de una sesión.

### Lo que el spike sacó a la luz y se corrigió

| Problema | Corrección |
| --- | --- |
| Latencia del modelo Pro | Cambio a Flash ([PR #5](https://github.com/juanjoseaosrivera/tera-pi/pull/5)) |
| Las pautas nunca dejaban de estar pendientes | Nueva tool `revisar-pauta` (`done`, `partial`, `dropped`) con el id de la pauta en las variables (PR #5) |
| "Cosas a recordar" duplicadas en cada sesión | El análisis pide solo datos nuevos y el backend descarta los casi iguales (PR #5) |
| El agente llamaba al usuario por su email | `DEV_AUTH_DISPLAY_NAME` en el login de prueba (PR #5) |
| Sesiones colgadas tras el saludo contaban como memoria | Estado `abandoned` si no hay ánimo ni pautas (PR #5) |
| No revisaba todas las pautas, descartaba las que la persona quería mantener y creaba duplicadas | Prompt más preciso y `guardar-pauta` ignora una pauta igual a otra pendiente ([PR #6](https://github.com/juanjoseaosrivera/tera-pi/pull/6)) |
| Si la persona seguía hablando, ElevenLabs cancelaba la herramienta | `interruption_mode: disable_during_tool` en todas las tools, reintento en el prompt y ánimo inicial también desde el análisis (PR #6) |
| El webhook creaba pautas a partir del análisis y confundía lo ya hecho con pautas nuevas | Las pautas solo se crean con `guardar-pauta` (PR #6) |

**Lección:** que la base de datos tenga sentido no basta; hay que leer las variables dinámicas que recibe el agente, porque es la memoria tal como él la ve. Casi todos los fallos se vieron ahí.

### Pendiente

**Antes de cualquier usuario fuera del círculo de pruebas:**

- [ ] **Batería de frases de riesgo** (regla 6) contra `gemini-2.5-flash`, con objetivo del 100 % de detección.
- [ ] Probar el caso "no hice la pauta, pero quiero seguir": el agente no debe marcarla como `dropped`.
- [ ] ADR de autenticación: sustituye al login de prueba (`ALLOW_DEV_AUTH`).
- [ ] Revisar la retención en ElevenLabs: las transcripciones se guardan 30 días y `delete_transcript_and_pii` está desactivado.
- [ ] Desactivar `file_input` en `agent.json` (está activo y no se usa).
- [ ] Quitar `http://localhost:4200` de `CORS_ORIGINS` en la VPS cuando exista el frontend.
- [ ] Confirmar los minutos del plan Starter en el panel.

**Menores:**

- [ ] El criterio `ciclo_sesion` da fallo si la persona rechaza acordar pautas nuevas; debería contarse como correcto.
- [ ] El agente a veces dice "ya lo he anotado", aunque el prompt le pide no mencionar que guarda datos.
- [ ] Una sesión cortada a los 2 minutos (25 de octubre) dejó un resumen poco útil en `resumen_previo`. Se irá solo, porque solo se usan las tres últimas sesiones.

## Referencias

- [ElevenLabs: Dynamic variables](https://elevenlabs.io/docs/eleven-agents/customization/personalization/dynamic-variables)
- [ElevenLabs: Server tools](https://elevenlabs.io/docs/eleven-agents/customization/tools/server-tools)
- [ElevenLabs: Post-call webhooks](https://elevenlabs.io/docs/agents-platform/workflows/post-call-webhooks)
- [ElevenLabs: Integrate your own model (Custom LLM)](https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm)
- [ElevenLabs: Data residency](https://elevenlabs.io/docs/de/overview/administration/data-residency)
