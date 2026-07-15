from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .loop import Agent, Config


def load_env_file(path: str | None) -> None:
    """Load only missing variables, so shell/desktop settings always win."""
    if not path:
        return
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def configuration_status(config: Config) -> dict[str, object]:
    """Report readiness without ever printing credentials."""
    required = {
        "LLM base URL (COTYPE_BASE_URL or MWS_BASE_URL)": config.llm_url,
        "LLM API key (COTYPE_API_KEY or MWS_API_KEY)": config.llm_key,
        "LLM model (COTYPE_MODEL, COTYPE_MODEL_NAME, or MWS_MODEL_NAME)": config.model,
    }
    return {
        "ready": not (missing := [label for label, value in required.items() if not value]),
        "missing": missing,
        "platformBaseUrl": config.base_url,
        "platformFrontendUrl": config.frontend_url,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Minimal MWS tool-calling bot agent")
    p.add_argument("prompt", nargs="?", help="What to create or change")
    p.add_argument("--env-file", help="Optional local provider .env; never copied to output")
    p.add_argument("--history-file")
    p.add_argument("--session-id")
    p.add_argument("--existing-bot-id")
    p.add_argument("--existing-version-id")
    p.add_argument("--existing-bot-name")
    p.add_argument("--existing-version-name")
    p.add_argument("--dry-run", action="store_true", help="Validate and save payload without a platform write")
    p.add_argument("--max-turns", type=int, default=16, help="Safety ceiling for tool/repair turns; verification-case count is chosen by the model")
    p.add_argument("--test-message", default="Hello")
    p.add_argument("--validate-payload", help="Validate a saved bot attributes JSON and exit")
    p.add_argument("--check-config", action="store_true", help="Check required LLM settings without printing secrets or calling a provider")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_env_file(args.env_file)
    config = Config.from_env(args)
    agent = Agent(config)
    if args.check_config:
        status = configuration_status(config)
        print(json.dumps(status, ensure_ascii=False))
        return 0 if status["ready"] else 2
    if args.validate_payload:
        payload = json.loads(Path(args.validate_payload).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = ((payload.get("data") or {}).get("attributes")) or payload
        errors = agent.validate(payload)
        print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 2
    if not args.prompt:
        parser().error('prompt is required unless --validate-payload or --check-config is used. Example: --env-file C:\\path\\to\\.env --dry-run "Create a simple FAQ bot"')
    try:
        agent.run(args.prompt)
    except Exception as error:
        print(f"Agent error: {error}", file=sys.stderr)
        return 1
    return 0
