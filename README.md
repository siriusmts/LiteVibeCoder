# Minimal MWS vibecoding agent

This is a clean, small tool-calling loop compatible with the supplied MWS desktop adapter. It deliberately keeps credentials out of the repository and has no third-party runtime dependency.

`SKILL.md` is the adapter-required skill index. The actual detachable skills live in `skills/`: `quality-loop` carries the platform-independent delivery method, while `mws-nocode` carries MWS routes, validation, headers, payload envelope, and frontend-link format. Replace the latter through `MWS_AGENT_PLATFORM_SKILL` without modifying the core loop. When the supplied GUI materializes a runtime copy, set `MTS_AGENT_DIR` to this source directory so the runtime loads the original skill pack.

The LLM chooses between platform inspection, drafting, structural validation, publication, and a post-publication engine test. Drafts are saved to `debug/last_platform_payload.json`; responses go to `debug/last_platform_response.json`.

## Run

```powershell
python create_mts_agent.py --env-file ..\..\work\starter_pack_2\starter_pack\.env --dry-run "Create a helpful assistant for our product"
python create_mts_agent.py --env-file ..\..\work\starter_pack_2\starter_pack\.env "Create a helpful assistant for our product"
```

The first command never changes the platform. For the desktop GUI, set `MTS_AGENT_DIR` to this folder; its adapter already supplies provider settings and passes `--dry-run` when upload is disabled.

The startup log should show `MCP TOOL: platform_contract` before drafting: that proves the selected detachable skill pack, rather than a platform prompt embedded in the loop, supplied the contract.

## Detachable MCP tools

`mws_mcp/` is a dependency-free stdio MCP server. The loop discovers its tool schemas using `initialize` and `tools/list`; it contains no MWS route or tool registry. Use `MWS_MCP_COMMAND` to replace it with another MCP server. In GUI mode, keep `MTS_AGENT_DIR` pointed at this source directory so the MCP child process can load the selected package. MCP JSON-RPC uses ASCII escaping on the wire so a malformed model Unicode character cannot break the server protocol.

## Tests

```powershell
$env:PYTHONPATH = (Get-Location)
python -m unittest discover -s tests -v
```

## Safety

- `.env`, `debug/`, token files, and generated payloads are ignored.
- Upload requires omitting `--dry-run`; validation happens before every write.
- The implementation contains general platform rules only—no benchmark-task instructions or task-specific templates.
