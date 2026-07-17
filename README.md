# Minimal MWS vibecoding agent

This is a clean, small tool-calling loop compatible with the supplied MWS desktop adapter. It deliberately keeps credentials out of the repository and has no third-party runtime dependency.

`SKILL.md` is the adapter-required skill index. The actual detachable skills live in `skills/`: `quality-loop` carries the platform-independent delivery method, while `mws-nocode` carries MWS routes, validation, headers, payload envelope, and frontend-link format. Replace the latter through `MWS_AGENT_PLATFORM_SKILL` without modifying the core loop. When the supplied GUI materializes a runtime copy, set `MTS_AGENT_DIR` to this source directory so the runtime loads the original skill pack.

The builder LLM chooses between platform inspection, drafting, structural validation, publication, and repair. After each real publication, an independent QA subagent using the same configured model derives observable requirements from the original request and tests the published bot through a real, stateful conversation. It chooses later messages from actual replies, buttons, commands, session state, and engine errors. A frontend link is returned only after every requirement has evidence and the QA verdict passes.

When QA finds a real behavior defect, it returns requirement-linked evidence and a concrete repair recommendation to the builder. The orchestration layer then enforces `get_saved_draft -> save_draft -> publish_draft` and starts a fresh QA run against the repaired version. A prose-only builder response cannot accidentally terminate this handoff. Live exploration is bounded from the number of planned requirements and capped at 12 messages, repeated actions are rejected, and the subagent must finish with a structured pass/fail verdict. These are domain-independent loop controls; no recipe, benchmark, or other task-specific behavior is encoded in the agent.

A rejected or unavailable MCP call is returned to the model as factual tool feedback, allowing it to recover with one of the discovered tools instead of terminating the run. Drafts are saved to `debug/last_platform_payload.json`; responses go to `debug/last_platform_response.json`.

## Run

```powershell
python create_mts_agent.py --env-file ..\..\work\starter_pack_2\starter_pack\.env --dry-run "Create a helpful assistant for our product"
python create_mts_agent.py --env-file ..\..\work\starter_pack_2\starter_pack\.env "Create a helpful assistant for our product"
```

The first command never changes the platform. For the desktop GUI, set `MTS_AGENT_DIR` to this folder; its adapter already supplies provider settings and passes `--dry-run` when upload is disabled.

## PyCharm: first safe run

In the Run Configuration, choose `create_mts_agent.py` as the script and add the following to **Parameters**:

```text
--env-file C:\Users\User\Desktop\starter_pack\.env --check-config
```

The command verifies that the required LLM URL, API key, and model are available without showing secrets or calling a provider. A successful check prints `"ready": true`. Then use a safe local draft run:

```text
--env-file C:\Users\User\Desktop\starter_pack\.env --dry-run "Create a helpful FAQ bot for an online store"
```

Only remove `--dry-run` when the draft run is satisfactory and you intend to publish to the MWS platform.

The startup log should show `MCP TOOL: platform_contract` before drafting: that proves the selected detachable skill pack, rather than a platform prompt embedded in the loop, supplied the contract.

The default emergency ceiling is 64 builder tool/repair turns (`--max-turns`); it is not a test-count limit or normal completion condition. `COTYPE_TIMEOUT` controls large builder requests and defaults to 600 seconds. Smaller QA requests use `MWS_VERIFIER_LLM_TIMEOUT` (120 seconds by default), with `MWS_VERIFIER_MAX_LIVE_TURNS` providing the hard exploration ceiling (12 by default). Set `MWS_VERIFICATION_MODE=legacy` only to restore the older inline verifier. When EVA provides `COTYPE_GENERATION_BASE_URL`, the agent uses it for model calls while preserving `COTYPE_BASE_URL` for the platform payload. A `Frontend URL:` line is emitted only after the published version passes independent QA.

## Detachable MCP tools

`mws_mcp/` is a dependency-free stdio MCP server. The loop discovers its tool schemas using `initialize` and `tools/list`; it contains no MWS route or tool registry. Use `MWS_MCP_COMMAND` to replace it with another MCP server. In GUI mode, keep `MTS_AGENT_DIR` pointed at this source directory so the MCP child process can load the selected package. MCP JSON-RPC uses ASCII escaping on the wire so a malformed model Unicode character cannot break the server protocol. `debug/last_run.json` records the run ID and its artifacts, so diagnostics do not accidentally pair a draft from one run with a response from another.

## Tests

```powershell
$env:PYTHONPATH = (Get-Location)
python -m unittest discover -s tests -v
```

## Safety

- `.env`, `debug/`, token files, and generated payloads are ignored.
- Upload requires omitting `--dry-run`; validation happens before every write.
- Updating an existing bot also requires a successful `inspect_existing_bot` call in the current MCP run; the runtime rejects publication otherwise.
- During a create-and-repair run, repairs become new versions of the first created bot. A later publication is rejected until the latest published version has been run through the verifier.
- The implementation contains general platform rules only—no benchmark-task instructions or task-specific templates.
