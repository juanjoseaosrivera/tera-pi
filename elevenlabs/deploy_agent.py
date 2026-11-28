"""Crea o actualiza en ElevenLabs todo lo que necesita el agente de Tera-pi.

Uso (desde la carpeta backend/, que ya tiene httpx instalado):

    uv run python ../elevenlabs/deploy_agent.py --dry-run   # qué enviaría, sin llamar a nada
    uv run python ../elevenlabs/deploy_agent.py             # crea o actualiza

Qué hace, en este orden (ADR-001, regla 1: la configuración vive en git):

1. Secreto del workspace con el valor de TOOLS_SHARED_SECRET. ElevenLabs lo envía en la
   cabecera X-Tool-Secret de cada server tool; el valor nunca aparece en el repositorio.
2. Webhook post-llamada (HMAC). Al crearlo, ElevenLabs devuelve su secreto UNA sola vez:
   el script lo muestra para que lo guardes en ELEVENLABS_WEBHOOK_SECRET.
3. Las server tools de tools.json.
4. El agente de agent.json con el prompt de prompt.md, las tools y el webhook.

Es idempotente: los ids de lo creado se guardan en state.json (no son secretos y se suben a
git), y en la siguiente ejecución se actualiza lo existente en vez de duplicarlo.

Necesita una API key de ADMINISTRACIÓN (ElevenAgents y webhooks con escritura). No es la
key del backend: esta solo se usa en tu PC para desplegar.
"""

import argparse
import copy
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
ELEVENLABS_API = "https://api.elevenlabs.io"
DEFAULT_API_BASE_URL = "https://api.tera-pi.pygmalion.cloud"

SECRET_NAME = "tera-pi-tools-secret"  # noqa: S105 (es el nombre, no el valor)
WEBHOOK_NAME = "tera-pi-post-call"


def load_json(name: str) -> dict[str, Any]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def strip_comments(value: Any) -> Any:
    """Quita las claves que empiezan por "_" (comentarios para humanos en los JSON)."""
    if isinstance(value, dict):
        return {k: strip_comments(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [strip_comments(v) for v in value]
    return value


def render(value: Any, replacements: dict[str, str]) -> Any:
    """Sustituye {API_BASE_URL} y {TOOL_SECRET_ID} en todas las cadenas."""
    if isinstance(value, dict):
        return {k: render(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, replacements) for v in value]
    if isinstance(value, str):
        for placeholder, real in replacements.items():
            value = value.replace("{" + placeholder + "}", real)
    return value


def secret_from_env_or_prompt(env_name: str, label: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        value = getpass.getpass(f"{label} (no se mostrará al escribir): ").strip()
    if not value:
        sys.exit(f"Falta {env_name}.")
    return value


class ElevenLabs:
    def __init__(self, api_key: str, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.client = httpx.Client(
            base_url=ELEVENLABS_API, headers={"xi-api-key": api_key}, timeout=30.0
        )

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.dry_run:
            print(f"\n[dry-run] {method} {path}")
            print(json.dumps(body, ensure_ascii=False, indent=2)[:3000])
            return {}
        response = self.client.request(method, path, json=body)
        if response.is_error:
            print(f"\nERROR {response.status_code} en {method} {path}:\n{response.text}")
            if response.status_code in (401, 403):
                print("Revisa que la API key tenga permisos de escritura en Agents y Webhooks.")
            sys.exit(1)
        return response.json() if response.content else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="muestra las peticiones sin enviarlas"
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL),
        help=f"URL pública del backend (por defecto {DEFAULT_API_BASE_URL})",
    )
    parser.add_argument(
        "--rotar-secreto-tools",
        action="store_true",
        help="actualiza en ElevenLabs el valor de TOOLS_SHARED_SECRET (tras cambiarlo en la VPS)",
    )
    args = parser.parse_args()

    api_base_url = args.api_base_url.rstrip("/")
    if not api_base_url.startswith("https://"):
        sys.exit("La URL del backend debe ser HTTPS: ElevenLabs no llama a HTTP ni a localhost.")

    state: dict[str, Any] = (
        json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    )
    state.setdefault("tool_ids", {})

    api_key = (
        "dry-run"
        if args.dry_run
        else secret_from_env_or_prompt(
            "ELEVENLABS_ADMIN_API_KEY", "API key de administración de ElevenLabs"
        )
    )
    el = ElevenLabs(api_key, args.dry_run)

    # 1. Secreto de las server tools ---------------------------------------------------
    if not state.get("tool_secret_id") or args.rotar_secreto_tools:
        value = (
            "dry-run"
            if args.dry_run
            else secret_from_env_or_prompt(
                "TOOLS_SHARED_SECRET",
                "TOOLS_SHARED_SECRET (el MISMO valor que en el .env de la VPS)",
            )
        )
        if state.get("tool_secret_id"):
            el.call(
                "PATCH",
                f"/v1/convai/secrets/{state['tool_secret_id']}",
                {"type": "update", "name": SECRET_NAME, "value": value},
            )
            print("Secreto de las tools actualizado.")
        else:
            created = el.call(
                "POST", "/v1/convai/secrets", {"type": "new", "name": SECRET_NAME, "value": value}
            )
            state["tool_secret_id"] = created.get("secret_id", "dry-run-secret-id")
            print(f"Secreto de las tools creado: {state['tool_secret_id']}")

    # 2. Webhook post-llamada ----------------------------------------------------------
    webhook_url = f"{api_base_url}/webhooks/elevenlabs"
    webhook_secret = None
    if not state.get("webhook_id"):
        created = el.call(
            "POST",
            "/v1/workspace/webhooks",
            {"settings": {"auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": webhook_url}},
        )
        state["webhook_id"] = created.get("webhook_id", "dry-run-webhook-id")
        webhook_secret = created.get("webhook_secret")
        print(f"Webhook post-llamada creado: {state['webhook_id']} -> {webhook_url}")
    elif state.get("webhook_url") != webhook_url:
        print(
            f"AVISO: el webhook existente apunta a {state.get('webhook_url')}. Si cambiaste la URL,"
            " bórralo en el panel, quita webhook_id de state.json y vuelve a ejecutar."
        )
    state["webhook_url"] = webhook_url

    # 3. Server tools ------------------------------------------------------------------
    replacements = {"API_BASE_URL": api_base_url, "TOOL_SECRET_ID": state["tool_secret_id"]}
    tools = render(strip_comments(load_json("tools.json"))["tools"], replacements)
    for tool in tools:
        name = tool["name"]
        tool_id = state["tool_ids"].get(name)
        if tool_id:
            el.call("PATCH", f"/v1/convai/tools/{tool_id}", {"tool_config": tool})
            print(f"Tool actualizada: {name}")
        else:
            created = el.call("POST", "/v1/convai/tools", {"tool_config": tool})
            state["tool_ids"][name] = created.get("id", f"dry-run-{name}")
            print(f"Tool creada: {name} ({state['tool_ids'][name]})")

    # 4. Agente ------------------------------------------------------------------------
    agent = copy.deepcopy(strip_comments(load_json("agent.json")))
    prompt = agent["conversation_config"]["agent"]["prompt"]
    prompt["prompt"] = (HERE / "prompt.md").read_text(encoding="utf-8")
    prompt["tool_ids"] = [state["tool_ids"][t["name"]] for t in tools]
    agent["platform_settings"]["workspace_overrides"] = {
        "webhooks": {
            "post_call_webhook_id": state["webhook_id"],
            # Solo el resumen y el análisis: ni audio ni transcripción (minimización de datos).
            "events": ["transcript"],
            "exclude_transcript": True,
        }
    }
    if state.get("agent_id"):
        el.call("PATCH", f"/v1/convai/agents/{state['agent_id']}", agent)
        print(f"Agente actualizado: {state['agent_id']}")
    else:
        created = el.call("POST", "/v1/convai/agents/create", agent)
        state["agent_id"] = created.get("agent_id", "dry-run-agent-id")
        print(f"Agente creado: {state['agent_id']}")

    if args.dry_run:
        print("\n[dry-run] No se ha enviado nada ni se ha modificado state.json.")
        return

    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"\nIds guardados en {STATE_FILE.name}. Súbelo a git (no contiene secretos).")
    print("\nValores para el .env de la VPS:")
    print(f"  ELEVENLABS_AGENT_ID={state['agent_id']}")
    if webhook_secret:
        print(f"  ELEVENLABS_WEBHOOK_SECRET={webhook_secret}")
        print(
            "\n  ATENCIÓN: ElevenLabs no volverá a mostrar este secreto."
            " Guárdalo ahora en tu gestor de contraseñas y en el .env de la VPS."
        )


if __name__ == "__main__":
    main()
