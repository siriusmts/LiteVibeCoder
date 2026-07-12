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
- Interactive bot version `6552` exposed a missing `target_node_id` on its init edge only at engine runtime. The graph validator now rejects every entry edge without a target; a repaired version is pending publication.
- A Hello World update exposed a model loop that used an unsupported edge shape before invoking `platform_contract`. The loaded machine-readable contract is now attached at the start of every model run, while invalid-draft reasons are printed safely for diagnosis.
- Skill-declared normalization now maps alternate entry-edge aliases before validation. This produced and published Hello World version `6579`, scenario `15351`; two independent inputs both returned exactly `Hello World!`.
- Interactive joke-bot version `6566`, scenario `15321`, is published. In one UTF-8 session it returned the category menu for `привет`, an IT joke plus `Ещё`/category-change buttons for `IT`, and another valid joke for text `Ещё`.

## 2026-07-12 — detachable MCP and DKS Sentiment

- MWS operations are now served by the standalone stdio MCP package `mws_mcp/`; `loop.py` discovers its tool schemas with `tools/list` and contains no MWS route or tool registry. MCP tests cover discovery, validation, normalization, Unicode safety, and absence of embedded platform tools.
- DKS dry-run produced the required two local scenarios, `extend` from scenario 1 to 2, LLM, script, and answer blocks. Skill validation now rejects incomplete LLM model configuration before publication.
- Published DKS version `6648` exposed a runtime requirement for `llm.system_message` and `llm.user_message`; those are now in the generic MWS skill contract. A follow-up network generation timed out before its first tool call, so no new version was published and DKS is explicitly not yet accepted as passing.

## 2026-07-12 — live DKS completion and autonomous safeguards

- Published DKS Sentiment bot `4084`, version `6682`, scenario `1`. It uses the task's exact topic, subtopic, and sentiment taxonomies, two linked scenarios, an LLM classifier, and sandbox-safe Python normalization.
- Independently exercised its live engine with neutral off-topic text, unavailable support, gratitude, and a negative service complaint. Each reply was nonempty JSON with the expected category/sentiment; a technical-error reply can no longer be treated as a passing smoke test.
- `test_published_bot` can now test a configured bot/version in a new MCP process, rather than relying on a previous publish in the same process.
- MCP debug records are isolated per run and secrets are redacted from both records and returned engine payloads.
- Rechecked Joke Bot `3929`, version `6566`: its category choices and follow-up choices are sent through `payload.suggestions.buttons`, while joke text is returned through `payload.items`; both are present in the real engine response.

## Next checkpoint

Run the dry-run against the provided model credentials, inspect the generated payload against the live platform, then run `hello-world` before adding features.
