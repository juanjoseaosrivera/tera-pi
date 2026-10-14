# Tera-pi · Backend

API en **Python 3.12 + FastAPI**. Cumple tres funciones (ver [ADR-001](../docs/adr/ADR-001-arquitectura-conversacion-voz.md) y [ADR-002](../docs/adr/ADR-002-base-de-datos-y-modelo-de-datos.md)):

1. **Inicia sesiones de voz:** crea la sesión, pide a ElevenLabs una *signed URL* y entrega al frontend la memoria de sesiones anteriores como variables dinámicas.
2. **Atiende las server tools** que el agente llama durante la conversación (ánimo, pautas, escalas, riesgo).
3. **Recibe el webhook post-llamada**, firmado con HMAC, y guarda el resumen y los datos extraídos (nunca la transcripción).

## Estructura

```
backend/
├── app/
│   ├── main.py              # crea la app FastAPI y registra las rutas
│   ├── core/
│   │   ├── config.py        # configuración desde variables de entorno (.env)
│   │   ├── crypto.py        # cifrado de campos sensibles (Fernet)
│   │   └── security.py      # firma HMAC del webhook y secreto de las server tools
│   ├── db/
│   │   ├── models.py        # tablas (SQLAlchemy 2)
│   │   └── session.py       # conexión y sesión por petición
│   ├── api/
│   │   ├── deps.py          # dependencias comunes (usuario actual, sesión de voz)
│   │   └── routes/          # health, sesiones, tools, webhooks
│   └── services/
│       ├── elevenlabs.py    # signed URL
│       ├── memory.py        # memoria → variables dinámicas del agente
│       └── scales.py        # PHQ-9, GAD-7, PSS-10: validación y puntuación
├── migrations/              # migraciones de Alembic
├── tests/                   # tests con SQLite en memoria (no necesitan MySQL)
├── .env.example             # plantilla de configuración
└── pyproject.toml / uv.lock # dependencias (gestionadas con uv)
```

La base de datos de desarrollo **no está aquí**: es el `docker-compose.yml` de la raíz del repo
(Percona Server 8.4, el mismo motor que CloudPanel), con sus contraseñas en el `.env` de la raíz.

```
tera-pi/
├── docker-compose.yml   # Percona 8.4 local (puerto 127.0.0.1:3306)
├── .env                 # MYSQL_ROOT_PASSWORD y MYSQL_PASSWORD (no va a git)
├── backend/
└── frontend/
```

## Endpoints

| Método | Ruta | Quién la llama | Protección |
| --- | --- | --- | --- |
| GET | `/health` | Monitorización | Pública |
| GET | `/health/db` | Monitorización | Pública |
| POST | `/api/sesiones` | Frontend, al empezar una sesión | Usuario (temporal: cabecera `X-Dev-User-Email`) |
| POST | `/tools/registrar-animo` | Agente de ElevenLabs | Cabecera `X-Tool-Secret` |
| POST | `/tools/guardar-pauta` | Agente de ElevenLabs | Cabecera `X-Tool-Secret` |
| POST | `/tools/registrar-respuesta-escala` | Agente de ElevenLabs | Cabecera `X-Tool-Secret` |
| POST | `/tools/registrar-riesgo` | Agente de ElevenLabs | Cabecera `X-Tool-Secret` |
| POST | `/webhooks/elevenlabs` | ElevenLabs, al terminar la llamada | Firma `ElevenLabs-Signature` |

En local, la documentación interactiva está en <http://localhost:8090/docs>. En producción está desactivada.

## Puesta en marcha en local (Windows, PowerShell)

**Requisitos:** [uv](https://docs.astral.sh/uv/) y Docker Desktop.

```powershell
# 1. Instalar uv (una sola vez)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Arrancar la base de datos (desde la RAÍZ del repo, no desde backend/)
cd C:\Users\juanj\OneDrive\Development\tera-pi
#    Si no existe el .env de la raíz: copy .env.example .env  y pon dos contraseñas
docker compose up -d
docker compose ps          # espera a ver (healthy)

# 3. Entrar en backend e instalar dependencias (uv descarga Python 3.12 si no lo tienes)
cd backend
uv sync

# 4. Crear tu configuración local
copy .env.example .env
#    En DATABASE_URL pon como contraseña el valor de MYSQL_PASSWORD del .env de la raíz.
#    Rellena DATA_ENCRYPTION_KEY y TOOLS_SHARED_SECRET (el fichero explica cómo generarlos).
#    Las variables de ElevenLabs pueden quedar con un valor provisional hasta tener el agente.

# 5. Crear las tablas
uv run alembic upgrade head

# 6. Arrancar la API
uv run uvicorn app.main:app --reload --port 8090
```

Abre <http://localhost:8090/health/db>: si responde `{"status":"ok"}`, la API habla con la base de datos.

## Tests y calidad

```powershell
uv run pytest          # tests
uv run ruff check .    # errores y malas prácticas
uv run ruff format .   # formato automático
```

## Migraciones de base de datos

Cada vez que cambies un modelo en `app/db/models.py`:

```powershell
uv run alembic revision --autogenerate -m "descripción del cambio"
# Revisa SIEMPRE el fichero generado en migrations/versions/ antes de aplicarlo
uv run alembic upgrade head
```

## Probar con el agente de ElevenLabs

ElevenLabs necesita llegar a la API por HTTPS desde internet para llamar a las server tools y al webhook, así que **no puede llamar a tu `localhost`**:

- **Despliegue en la VPS** (`https://api.tera-pi.pygmalion.cloud`): ver [Infra 03](../docs/infra/03-despliegue-backend.md).
- **Configuración del agente:** ver [`elevenlabs/README.md`](../elevenlabs/README.md).
- **Página de prueba del spike:** `spike/index.html` (cómo abrirla en Infra 03, paso 8).
- Para pruebas puntuales en local, un túnel temporal (por ejemplo `cloudflared tunnel --url http://localhost:8090`). En ese caso, despliega el agente con `--api-base-url` apuntando al túnel y recuerda volver a desplegarlo con la URL de la VPS.

## Login de prueba (temporal)

Hasta el ADR de autenticación, `POST /api/sesiones` identifica al usuario con cabeceras:

| Variable | Cabecera | En local | En producción |
| --- | --- | --- | --- |
| `ALLOW_DEV_AUTH` | — | `true` | `true` solo durante el spike |
| `DEV_AUTH_ALLOWED_EMAILS` | `X-Dev-User-Email` | Vacía (cualquier email) | Obligatoria |
| `DEV_AUTH_TOKEN` | `X-Dev-Token` | Vacía (sin token) | Obligatoria, 32 caracteres o más |

Si en producción falta alguna de las dos, la API no arranca.

## Pendiente

- Autenticación real de usuarios (ADR propio). Mientras tanto, el login de prueba de arriba.
- Alerta (correo o Telegram) cuando se registre un evento de riesgo.
