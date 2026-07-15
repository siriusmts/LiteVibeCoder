# Minimal MWS vibecoding agent

This is a clean, small tool-calling loop compatible with the supplied MWS desktop adapter. It deliberately keeps credentials out of the repository and has no third-party runtime dependency.

`SKILL.md` is the adapter-required skill index. The actual detachable skills live in `skills/`: `quality-loop` carries the platform-independent delivery method, while `mws-nocode` carries MWS routes, validation, headers, payload envelope, and frontend-link format. Replace the latter through `MWS_AGENT_PLATFORM_SKILL` without modifying the core loop. When the supplied GUI materializes a runtime copy, set `MTS_AGENT_DIR` to this source directory so the runtime loads the original skill pack.

The LLM chooses between platform inspection, drafting, structural validation, publication, and a coverage-driven post-publication verification plan. It decides how many named black-box cases are necessary from the task's observable paths; cases can contain ordered shared-session steps and text, positive-regex, forbidden-regex, button, or command assertions. Success requires the MCP verifier to pass them all. A rejected or unavailable MCP call is returned to the model as factual tool feedback, allowing it to recover with one of the discovered tools instead of terminating the run. Drafts are saved to `debug/last_platform_payload.json`; responses go to `debug/last_platform_response.json`.

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

The default safety ceiling is 16 tool/repair turns (`--max-turns`); it is not a test-count limit. The model decides the verification suite size from the task. `COTYPE_TIMEOUT` defaults to 180 seconds and can be reduced by a runner when needed. A `Frontend URL:` line is emitted only after the published version passes the verification suite.

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
