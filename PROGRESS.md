# Progress journal

## 2026-07-12 — minimal baseline

- Started a fresh implementation rather than modifying the supplied reference agent.
- Added a standard-library OpenAI-compatible tool-calling loop with inspection, draft storage, validation, publish/update, and engine-test tools.
- Kept the supplied GUI contract: `create_mts_agent.py`, `mws_agent/`, CLI history/session/existing-bot flags, and adapter-readable `payload:`, `POST status:`, and `TEST status:` log lines.
- Platform writes are guarded by `--dry-run`; no secret is written into this project.
- Initial benchmark target: `hello-world`, then `joke-bot` / `quick2`. Remote benchmark execution needs its separate account/token settings and is not yet run.

## 2026-07-12 — live-contract feedback

- A dry-run generated a locally valid payload without writing to the platform.
- The first live import returned HTTP 422 because the platform additionally requires version `name` and `changesMessage`. The shared skill and validator now require them, plus the documented LangGraph graph shape.
- A subsequent model request timed out before a payload was generated. The client now retries one transient model failure and flushes lifecycle logs immediately.

## Next checkpoint

Run the dry-run against the provided model credentials, inspect the generated payload against the live platform, then run `hello-world` before adding features.
