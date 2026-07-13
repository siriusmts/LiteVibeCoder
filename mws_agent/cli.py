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
    p.add_argument("--max-turns", type=int, default=20)
    p.add_argument("--test-message", default="Hello")
    p.add_argument("--validate-payload", help="Validate a saved bot attributes JSON and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_env_file(args.env_file)
    config = Config.from_env(args)
    agent = Agent(config)
    if args.validate_payload:
        payload = json.loads(Path(args.validate_payload).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = ((payload.get("data") or {}).get("attributes")) or payload
        errors = agent.validate(payload)
        print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 2
    prompt = args.prompt or os.getenv("MWS_AGENT_PROMPT") or os.getenv("EVA_PROMPT")
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        parser().error("prompt is required unless --validate-payload is used")
    try:
        agent.run(prompt)
    except Exception as error:
        print(f"Agent error: {error}", file=sys.stderr)
        return 1
    return 0
