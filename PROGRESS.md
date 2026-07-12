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
- A successful import of bot `3927`, version `6481` established that an imported version must also be published before its engine can execute. After `publish`, an independent `Any message` smoke test returned HTTP 200 and `Hello World`.
- The next simple scenario created bot `3929`, version `6483`. Its post-publish smoke test and three independent retries returned HTTP 200 with a short safe joke. Engine tests now retry one transient 5xx and print only the visible bot reply.

## 2026-07-12 — modular skill correction

- Moved the platform contract out of the core loop: `skills/quality-loop` is platform-independent, while `skills/mws-nocode` contains the MWS instructions and `platform.json` with routes, headers, envelope, validation, response shape, and frontend-link format.
- The runtime accepts `MWS_AGENT_PLATFORM_SKILL` and `MWS_AGENT_WORK_STYLE_SKILL`; `MTS_AGENT_DIR` keeps the skills available when the supplied GUI materializes its runtime copy.
- Frontend links now require and include `activeScenarioId`. Existing live links are `/projects/3927?botVersionId=6481&activeScenarioId=15142` and `/projects/3929?botVersionId=6483&activeScenarioId=15146`.
- A GUI-mode dry-run revealed that a text-only skill was insufficient for model construction. `platform_contract` now returns the loaded payload and validation sections from the selected skill pack; tool names are logged so stalled loops remain diagnosable without exposing secrets.
- Update mode was exercised against existing bot `3927` in dry-run. The sequence was `inspect_existing_bot → save_draft → platform_contract → save_draft → publish_draft`; the updated payload validated and no platform write was made.

## Next checkpoint

Run the dry-run against the provided model credentials, inspect the generated payload against the live platform, then run `hello-world` before adding features.
