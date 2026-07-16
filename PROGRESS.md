# Progress journal

## 2026-07-15 вЂ” PyCharm readiness check

- Added `--check-config`: it verifies that an LLM endpoint, key, and model are configured without exposing credentials or contacting a provider.
- Documented a safe first PyCharm run (`--check-config`, then `--dry-run`) and ignored local `.idea/` and `.venv/` directories.
- Ran the check against the supplied local `.env`; it reported `ready: true`. The standard-library suite passed 23 tests.
- A safe end-to-end run exposed an early exit because the context tool also reports `dryRun: true`. The loop now completes only when the publication tool returns that flag; a regression test covers the full context → draft → validation → dry-run publication sequence.
- Re-ran the supplied local configuration after the repair: `inspect_existing_bot` → `platform_contract` → `save_draft` → `validate_draft` → `publish_draft`. The run finished as a dry-run with no platform write; the suite passed 24 tests.
- A live run revealed a repair-loop hazard: after a verification failure, the next publication created a new bot rather than a version of the first bot, and the model could publish twice without verifying the intervening version. The runtime now pins follow-up repairs to the first created bot and the loop requires verification between successful publications. Regression coverage covers both conditions; the suite passed 26 tests.
- Strengthened generated-graph validation: empty answers, empty menus, duplicate node IDs, and unreachable local nodes now return actionable draft errors before publication. An interactive dry-run produced one valid three-node scenario with three menus and three answers; the expanded suite passed 29 tests.
- Added `debug/last_run.json` and per-run manifests so draft and platform-response artifacts can be traced to the same run during diagnosis.
- Reviewed the EVA analytics report for Joke Bot. Its hard checks were 3/3 and score 10/10, but EVA stopped the process after seeing a frontend link and recorded a nonzero exit code; the optional LLM judge was also disabled because its separate `API_TOKEN` was absent. The report used an older bundled `vendor/Agents.git` commit, not this worktree.
- Deferred `Frontend URL` output until post-publication verification passes, preventing link-driven benchmark runners from terminating the agent mid-cycle. New-bot imports now recover from platform duplicate-name errors with a bounded local suffix retry. Regression coverage passed 30 tests.
- Reviewed the full EVA batch against commit `597d36f`: 11 of 60 recorded task runs passed, while most remaining failures were LLM timeouts or verification-repair cycles stopped at the previous 16-turn ceiling. EVA did connect to the correct local Git commit and verified the copied source tree.
- The runtime now honors EVA's `COTYPE_GENERATION_BASE_URL` token-proxy endpoint for LLM calls while retaining `COTYPE_BASE_URL` for bot payloads, and the default repair ceiling is 24 turns. Tests cover both URL separation and the new limit.
- Raised the emergency tool/repair ceiling to 64 turns. Successful verification remains the normal completion condition chosen by the model; the ceiling only stops a genuinely stalled run.

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

## 2026-07-12 — higher-complexity Support Bot

- Extended the detachable MWS skill with validated `single_if` routes and documented stateful routing plus `go_operator` handoff. The generic loop remains unchanged.
- Published Support Bot `4110`, version `6698`, scenario `1`: six scenarios, `init`/`no_match` routing, LLM catalog dialogue, local interactive menu, `extend` transitions, and a terminal operator handoff.
- Live engine checks passed for greeting/menu, direct `Тарифы` (returns 990 and 490), a free tariff question, and an operator request (returns the handoff message and a `go_operator` command).

## Next checkpoint

Use this validated routing pattern for the more demanding HR Copilot and booking tasks.

## 2026-07-12 — coverage-driven HR Copilot

- The autonomous verifier now accepts a model-selected, named test plan. It has no fixed case count: the task's observable branches determine coverage, and all named cases must pass before completion.
- Published HR Copilot `4119`, version `6712`, scenario `1`: five linked scenarios for HR dialogue, response/menu, operator handoff, prompt-injection recovery, and interview slots.
- Its live verification plan covered greeting/menu, salary FAQ, operator command, injection recovery, and scheduling. An initial UTF-8 transport failure was reported as a failed plan, repaired, and the same five cases then passed.

## 2026-07-12 — MEDSI MCP preflight

- Added generic validation and environment-materialisation for `agent` blocks with external MCP servers; the loop and work-style remain platform/domain neutral.
- Discovered the live MEDSI MCP schema over SSE and published the profile-first integration bot `4130`, version `6716`. Its engine greeting invoked the real `get_profile` tool and returned the patient name `Аркадий Антонович`.
- The booking branches are deliberately not marked complete yet: the remaining implementation must preserve the real-MCP-only, explicit-confirmation, and real-`REC-...` invariants.

## 2026-07-13 — stateful verification

- Verification cases now support ordered steps in one shared engine session, so confirmation gates and other multi-turn behavior are checked through the public interface without domain-specific logic in the loop.
- The generic verifier also accepts positive and forbidden regular expressions, allowing a task-defined plan to assert both required visible evidence and forbidden premature-success text.
- Published MEDSI Booking MCP `4130`, version `6893`, scenario `1`: [open bot](http://5.188.27.251:18080/projects/4130?botVersionId=6893&activeScenarioId=1). Live sessions passed profile greeting, self booking through explicit confirmation, no appointment before confirmation, change-time return to real slots, child/ENT clinic discovery, dentistry subtype clarification, and unsupported-city regional contact. A separate explicit confirmation produced the real appointment `REC-5DF13C86`.

## 2026-07-15 вЂ” update safety gate

- Update publication now requires a successful `inspect_existing_bot` call for the selected bot in the current MCP run. This converts the existing inspect-first instruction into an enforceable safety condition and prevents an inspection of one bot from authorizing another bot's update.
- Added focused regression coverage for both the required inspection and cross-bot reset; the existing standard-library integration suite remains the acceptance check for this checkpoint.
