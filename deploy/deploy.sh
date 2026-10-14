#!/usr/bin/env bash
# Despliega la última versión de la rama main en la VPS.
#
# Se ejecuta con tu usuario de administración (juanjoadmin), no como root ni como el usuario
# del site:   sudo bash /home/pygmalion-api-tera-pi/tera-pi/deploy/deploy.sh
#
# Pasos: git pull → dependencias → migraciones → reinicio → comprobación de salud.
# Si cualquier paso falla, el script se detiene (set -e) y el servicio sigue con la versión anterior.
set -euo pipefail

SITE_USER="pygmalion-api-tera-pi"
APP_DIR="/home/${SITE_USER}/tera-pi"
BRANCH="${1:-main}"

as_site() {
  # Todo lo que toca el código se hace como el usuario del site, nunca como root.
  sudo -u "${SITE_USER}" -H bash -c "export PATH=\$HOME/.local/bin:\$PATH; cd '${APP_DIR}' && $1"
}

echo "==> Código (${BRANCH})"
as_site "git fetch --prune origin && git checkout '${BRANCH}' && git pull --ff-only origin '${BRANCH}'"
as_site "git log -1 --format='    %h %s (%an, %ad)' --date=short"

echo "==> Dependencias"
# --frozen: instala exactamente lo de uv.lock. --no-dev: sin pytest ni ruff en producción.
as_site "cd backend && uv sync --frozen --no-dev"

echo "==> Migraciones"
as_site "cd backend && .venv/bin/alembic upgrade head"

echo "==> Reinicio"
systemctl restart tera-pi-api

echo "==> Comprobación"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8090/health/db >/dev/null 2>&1; then
    echo "    OK: $(curl -fsS http://127.0.0.1:8090/health/db)"
    exit 0
  fi
  sleep 1
done
echo "    La API no responde. Últimas líneas del log:"
journalctl -u tera-pi-api -n 30 --no-pager
exit 1
