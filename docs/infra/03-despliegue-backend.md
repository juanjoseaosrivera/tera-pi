# Infra 03: Despliegue del backend en la VPS

Guía para poner la API de Tera-pi en `https://api.tera-pi.pygmalion.cloud`. Es lo que necesita el spike del [ADR-001](../adr/ADR-001-arquitectura-conversacion-voz.md): ElevenLabs tiene que llamar a las server tools y al webhook por HTTPS desde internet, y no puede llegar a tu `localhost`.

**Al terminar tendrás:**

| Pieza | Dónde |
| --- | --- |
| Código | `/home/pygmalion-api-tera-pi/tera-pi` (clon de git, **fuera** de `htdocs`) |
| Configuración y secretos | `/home/pygmalion-api-tera-pi/tera-pi/backend/.env` (permisos `600`) |
| Proceso | Servicio de systemd `tera-pi-api`: uvicorn en `127.0.0.1:8090`, como el usuario del site |
| Entrada desde internet | Nginx de CloudPanel: HTTPS en el 443 → `127.0.0.1:8090` (ya creado en [Infra 01](01-subdominios-y-sites.md)) |
| Agente | ElevenLabs, configurado con `elevenlabs/deploy_agent.py` |

**Tiempo estimado:** 60–90 minutos.

**Requisitos (ya hechos):** site Python `api.tera-pi.pygmalion.cloud` con usuario `pygmalion-api-tera-pi` y puerto 8090, certificado de Let's Encrypt, base de datos `terapi` con usuario `terapiapp` y acceso SSH con `juanjoadmin` ([Infra 01](01-subdominios-y-sites.md) e [Infra 02](02-seguridad-servidor.md)).

```mermaid
flowchart LR
    PC[Tu PC] -- "git push" --> GH[GitHub]
    GH -- "git pull (deploy key, solo lectura)" --> APP
    subgraph VPS
        NG[Nginx :443] --> APP[uvicorn :8090<br/>usuario del site]
        APP --> DB[(MySQL :3306<br/>solo localhost)]
    end
    EL[ElevenLabs] -- "tools + webhook (HTTPS)" --> NG
```

---

## Paso 0: Antes de empezar

1. **Snapshot de la VPS** en hPanel (como en Infra 01). Si algo sale mal, vuelves atrás en minutos.
2. **Sube a GitHub lo que hay en tu PC.** El servidor descarga el código desde GitHub, no desde tu PC. En este paso tienes que llevar la rama con esta guía hasta `develop` (ver "Subirlo a git" al final).
3. Ten a mano el gestor de contraseñas: vas a crear tres secretos nuevos y a usar la contraseña de la base de datos.

## Paso 1: Clave de despliegue de GitHub (deploy key)

El servidor necesita leer el repositorio. Para eso **no** se usan tu usuario ni tu contraseña de GitHub, sino una *deploy key*: una clave SSH que solo da acceso de **lectura** a **este** repositorio. Si alguien la robara del servidor, no podría tocar tu cuenta ni escribir en el repo.

```bash
ssh juanjoadmin@93.127.162.184
sudo -iu pygmalion-api-tera-pi        # trabajar como el usuario del site
ssh-keygen -t ed25519 -C "tera-pi VPS (solo lectura)" -f ~/.ssh/github_tera_pi -N ""
cat ~/.ssh/github_tera_pi.pub
```

> `-N ""` crea la clave sin frase de paso, porque el despliegue no es interactivo. La protegen los permisos: solo el usuario del site puede leerla.

1. Copia la línea que empieza por `ssh-ed25519`.
2. En GitHub: repo `tera-pi` → **Settings** → **Deploy keys** → **Add deploy key**.
3. Title: `VPS Hostinger (solo lectura)`. Pega la clave. **No marques "Allow write access".** Pulsa **Add key**.

Indica a SSH que use esa clave para GitHub:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/github_tera_pi
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com
```

La primera vez, SSH pregunta si confías en el servidor. **Comprueba que la huella es exactamente** `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU` (la ED25519 que [publica GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)) y escribe `yes`. Debe responder:

```
Hi juanjoseaosrivera/tera-pi! You've successfully authenticated, but GitHub does not provide shell access.
```

## Paso 2: Clonar el repositorio fuera de `htdocs`

```bash
cd ~
git clone git@github.com:juanjoseaosrivera/tera-pi.git
cd tera-pi
git checkout develop
git log -1 --oneline
```

> **Por qué fuera de `htdocs`:** `htdocs` es la carpeta que Nginx puede servir. Si el código estuviera ahí, un fallo de configuración podría publicar ficheros del repositorio, o incluso el `.env`. En `~/tera-pi` Nginx no tiene ninguna ruta hacia ellos: el único camino desde internet es el proxy hacia uvicorn. `htdocs` se queda vacía.

## Paso 3: Instalar uv y las dependencias

`uv` se instala solo para el usuario del site, en `~/.local/bin`, sin `sudo`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv --version

cd ~/tera-pi/backend
uv sync --frozen --no-dev
```

- `--frozen`: instala exactamente las versiones de `uv.lock`, las mismas con las que pasaron los tests. Si `uv.lock` no cuadra con `pyproject.toml`, falla en vez de "arreglarlo" por su cuenta.
- `--no-dev`: sin pytest ni ruff. En producción solo va lo necesario.

## Paso 4: Crear el `.env` de producción

Genera tres secretos **nuevos**. No reutilices los de tu `.env` local: si tu PC se viera comprometido, producción seguiría a salvo.

```bash
cd ~/tera-pi/backend
.venv/bin/python -c "from cryptography.fernet import Fernet; print('DATA_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
.venv/bin/python -c "import secrets; print('TOOLS_SHARED_SECRET=' + secrets.token_urlsafe(32))"
.venv/bin/python -c "import secrets; print('DEV_AUTH_TOKEN=' + secrets.token_urlsafe(32))"
```

**Guarda los tres en tu gestor de contraseñas ahora.** `DATA_ENCRYPTION_KEY` es especialmente crítica: si se pierde, los resúmenes y pautas cifrados no se pueden recuperar.

Crea el fichero con permisos restringidos **antes** de escribir nada en él:

```bash
install -m 600 /dev/null .env
nano .env
```

Contenido (sustituye lo que va en MAYÚSCULAS):

```ini
ENVIRONMENT=production

# Contraseña de terapiapp. Si tiene @ : / % #, escríbela codificada (@ = %40, : = %3A, / = %2F, % = %25, # = %23)
DATABASE_URL=mysql+pymysql://terapiapp:CONTRASEÑA_BD@127.0.0.1:3306/terapi?charset=utf8mb4

DATA_ENCRYPTION_KEY=EL_GENERADO
TOOLS_SHARED_SECRET=EL_GENERADO

# Se rellenan en el paso 7. Mientras, "pendiente" deja arrancar la API.
ELEVENLABS_API_KEY=pendiente
ELEVENLABS_AGENT_ID=pendiente
ELEVENLABS_WEBHOOK_SECRET=pendiente

# localhost:4200 solo durante el spike (para la página de prueba). Quitarlo después.
CORS_ORIGINS=https://tera-pi.pygmalion.cloud,http://localhost:4200

# TEMPORAL: login de prueba hasta el ADR de autenticación.
ALLOW_DEV_AUTH=true
DEV_AUTH_ALLOWED_EMAILS=TU_EMAIL
DEV_AUTH_TOKEN=EL_GENERADO
```

Guarda con `Ctrl+O`, `Enter`, y sal con `Ctrl+X`. Comprueba los permisos:

```bash
ls -l .env      # debe empezar por -rw------- y pertenecer a pygmalion-api-tera-pi
```

> **El login de prueba en un servidor público** es un riesgo asumido solo para el spike, con tres frenos: solo los emails de la lista, el token secreto en la cabecera `X-Dev-Token` y los límites del agente (2 llamadas simultáneas, 20 al día). La API **no arranca** en producción con `ALLOW_DEV_AUTH=true` si falta la lista o el token. Antes de enseñar Tera-pi a nadie, toca el ADR de autenticación.

## Paso 5: Crear las tablas

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current      # debe mostrar el id de la última migración con "(head)"
```

Si falla con `Access denied`, revisa la contraseña de `DATABASE_URL` (y su codificación). Si falla con `Can't connect`, comprueba que MySQL está activo: `systemctl status mysql`.

## Paso 6: Servicio de systemd

systemd arranca la API al iniciar el servidor, la reinicia si se cae y guarda sus logs. Vuelve a tu usuario de administración:

```bash
exit                                   # deja de ser pygmalion-api-tera-pi
sudo cp /home/pygmalion-api-tera-pi/tera-pi/deploy/tera-pi-api.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/tera-pi-api.service   # sin salida = correcto
sudo systemctl daemon-reload
sudo systemctl enable --now tera-pi-api
systemctl status tera-pi-api --no-pager
curl -s http://127.0.0.1:8090/health/db
```

Debe aparecer `active (running)` y `{"status":"ok"}`.

Lee el fichero `deploy/tera-pi-api.service`: cada línea está comentada. Lo importante es que el proceso corre como el usuario del site (no como root), solo escucha en `127.0.0.1` y está "encerrado": no puede escribir en ningún sitio del disco ni ganar privilegios.

**Comprobación desde tu PC** (PowerShell; usa `curl.exe`, no el alias `curl`):

```powershell
curl.exe -s https://api.tera-pi.pygmalion.cloud/health/db
# {"status":"ok"}

curl.exe -s -o NUL -w "%{http_code}`n" -X POST https://api.tera-pi.pygmalion.cloud/tools/registrar-animo
# 401  ← sin el secreto, las tools rechazan la petición

curl.exe -s -o NUL -w "%{http_code}`n" https://api.tera-pi.pygmalion.cloud/docs
# 404  ← en producción la documentación interactiva no se publica
```

Y en la VPS, que el secreto correcto sí pasa (la sesión no existe, así que debe responder 404 y no 401):

```bash
sudo -u pygmalion-api-tera-pi bash -c '
  SECRET=$(grep ^TOOLS_SHARED_SECRET= ~/tera-pi/backend/.env | cut -d= -f2-)
  curl -s -X POST https://api.tera-pi.pygmalion.cloud/tools/registrar-animo \
    -H "Content-Type: application/json" -H "X-Tool-Secret: $SECRET" \
    -d "{\"session_id\":\"no-existe\",\"momento\":\"inicio\",\"valor\":5}"'
# {"detail":"Sesión no encontrada"}
```

La API ya está en internet. El 502 de Infra 01 ha desaparecido.

## Paso 7: Crear el agente y completar el `.env`

En tu PC, sigue [`elevenlabs/README.md`](../../elevenlabs/README.md):

1. Crea las dos API keys (`tera-pi-admin` y `tera-pi-backend`).
2. Ejecuta `deploy_agent.py`. Cuando pida `TOOLS_SHARED_SECRET`, usa **el del paso 4**.
3. Apunta `ELEVENLABS_AGENT_ID` y `ELEVENLABS_WEBHOOK_SECRET`.

En la VPS, sustituye los tres `pendiente`:

```bash
sudo -iu pygmalion-api-tera-pi
nano ~/tera-pi/backend/.env
#   ELEVENLABS_API_KEY=        ← la clave tera-pi-backend (no la de administración)
#   ELEVENLABS_AGENT_ID=       ← la que mostró el script
#   ELEVENLABS_WEBHOOK_SECRET= ← la que mostró el script
exit
sudo systemctl restart tera-pi-api
systemctl status tera-pi-api --no-pager
```

## Paso 8: Prueba completa (el spike)

En tu PC:

```powershell
cd C:\Users\juanj\OneDrive\Development\tera-pi\backend
uv run python -m http.server 4200 --bind 127.0.0.1 -d ..\spike
```

Abre <http://localhost:4200>, escribe tu email y el `DEV_AUTH_TOKEN`, pulsa **Empezar sesión** y permite el micrófono. Mientras hablas, en otra terminal SSH mira el log en directo:

```bash
sudo journalctl -u tera-pi-api -f
```

Lo que debes ver, en orden:

| Momento | En el log de la VPS | En la página |
| --- | --- | --- |
| Pulsas Empezar | `POST /api/sesiones 201` | Las variables dinámicas y "Conectado" |
| Dices tu ánimo | `POST /tools/registrar-animo 200` | Bloque "Herramienta: registrar-animo" |
| Acordáis una pauta | `POST /tools/guardar-pauta 200` | Bloque "Herramienta: guardar-pauta" |
| Te despides y cuelga | — | "Desconectado" |
| 1–2 min después | `POST /webhooks/elevenlabs 200` | — |

Después, en la base de datos (la contraseña de `terapiapp`):

```bash
mysql -u terapiapp -p terapi -e "SELECT id, status, mood_start, mood_end, risk_flag, elevenlabs_conversation_id IS NOT NULL AS con_webhook FROM voice_sessions ORDER BY started_at DESC LIMIT 3;"
```

La última sesión debe estar `completed`, con los dos ánimos y `con_webhook = 1`. El resumen y las pautas no se leen en claro: están cifrados, como manda el ADR-002.

Abre una segunda sesión: el agente debe acordarse de la primera (resumen y pautas pendientes). Ese es el circuito completo del ADR-001.

## Paso 9: Siguientes despliegues

Cada vez que fusiones cambios del backend en `develop`:

```bash
ssh juanjoadmin@93.127.162.184
sudo bash /home/pygmalion-api-tera-pi/tera-pi/deploy/deploy.sh develop
```

El script hace `git pull`, instala dependencias, aplica migraciones, reinicia y comprueba `/health/db`. Si falla un paso, se detiene. Cuando haya rama `main` estable, se desplegará con `deploy.sh` sin argumento.

**Volver a la versión anterior** si algo se rompe:

```bash
sudo -iu pygmalion-api-tera-pi
cd ~/tera-pi && git log --oneline -5      # elige el commit bueno
git checkout <commit>
cd backend && uv sync --frozen --no-dev
exit
sudo systemctl restart tera-pi-api
```

Si el despliegue malo incluía una migración, antes hay que deshacerla con `.venv/bin/alembic downgrade -1`. Revisa siempre qué hace esa migración antes de ejecutarla.

## Problemas frecuentes

| Síntoma | Causa probable | Qué mirar |
| --- | --- | --- |
| 502 Bad Gateway | El servicio no está arrancado | `systemctl status tera-pi-api` y `journalctl -u tera-pi-api -n 50` |
| El servicio se reinicia en bucle con `ValidationError` | Falta una variable del `.env`, o falta la lista de emails o el token | El mensaje dice qué variable |
| `POST /api/sesiones` → 401 `Token no válido` | `X-Dev-Token` no coincide con `DEV_AUTH_TOKEN` | El token de la página de prueba |
| `POST /api/sesiones` → 403 | Tu email no está en `DEV_AUTH_ALLOWED_EMAILS` | Mayúsculas no importan; espacios y erratas sí |
| `POST /api/sesiones` → 502 | ElevenLabs rechaza la API key o el agent id | El log muestra el código de ElevenLabs |
| Error de CORS en la consola del navegador | El origen de la página no está en `CORS_ORIGINS` | Debe ser exactamente `http://localhost:4200` |
| Tools → 401 en el log | `TOOLS_SHARED_SECRET` distinto en la VPS y en ElevenLabs | `deploy_agent.py --rotar-secreto-tools` |
| Webhook → 401 en el log | `ELEVENLABS_WEBHOOK_SECRET` incorrecto | Recrear el webhook (ver `elevenlabs/README.md`) |
| El agente no llama a las tools | Prompt o modelo | En ElevenLabs → Agents → Tera-pi → **Conversations**, abre la llamada y revisa las tool calls |

## Checklist

- [ ] Snapshot de la VPS
- [ ] Deploy key de solo lectura en GitHub y `ssh -T git@github.com` correcto
- [ ] Repo clonado en `/home/pygmalion-api-tera-pi/tera-pi`, rama `develop`
- [ ] `uv sync --frozen --no-dev` sin errores
- [ ] `.env` con permisos `600` y los tres secretos guardados en el gestor
- [ ] `alembic current` muestra `(head)`
- [ ] Servicio `tera-pi-api` activo y habilitado
- [ ] `https://api.tera-pi.pygmalion.cloud/health/db` → `{"status":"ok"}`, tools sin secreto → 401, `/docs` → 404
- [ ] Agente creado con `deploy_agent.py` y `state.json` en git
- [ ] `.env` sin ningún `pendiente` y servicio reiniciado
- [ ] Sesión de prueba completa: tools 200, webhook 200, sesión `completed`
- [ ] Segunda sesión: el agente recuerda la primera
