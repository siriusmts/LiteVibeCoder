# MWS no-code platform

This skill supplies the MWS-specific contract to the generic agent loop. It can be replaced as a unit by another directory containing this file and `platform.json`; set `MWS_AGENT_PLATFORM_SKILL` to that directory.

## Delivery flow

For a new bot: import the validated draft, publish the returned version, then send an engine message with a fresh session and message ID. For an existing bot: successfully inspect the selected bot through `inspect_existing_bot`, import a version, make it current, publish it, then test it. The MCP runtime rejects an update publication when that successful inspection has not occurred in the current run.

After the first successful publication in a create run, repairs are imported as new versions of that created bot rather than as separate bots. Verify every published version before publishing another repair; the loop rejects an additional publication while verification of the latest one is pending.

Report the frontend URL with `botVersionId` and the first returned scenario `id` as `activeScenarioId`. A version is not ready merely because import succeeds.

## Payload rules

Use the fields, routes, envelope, and validation rules in `platform.json`. They are machine-readable precisely so that platform details remain outside the generic loop. Do not put API keys or bearer tokens in skills, payloads, or logs.

## Interactive flows

For a menu or branching dialogue, use a `buttons` block with `buttons: [{"title": "…", "target_node_id": "…"}]`, followed by `wait_for_user`. A target must name a node in the same scenario. A node can answer, display another menu, or route to a subsequent node. This is a general interaction pattern; choose the actual menu items and content from the user's request.

## Multiple scenarios

When a request needs multiple scenarios, give each scenario a distinct local integer `id`. Link them through an `extend` block using `scenario_id` equal to the target scenario's local id. The source node still needs a normal node id and block list. Do not use IDs from an unrelated, already-published bot.

## LLM and script blocks

An `llm` block needs `system_message`, `user_message`, `result_variable_name`, and `model`. Put the detailed classifier instruction in `system_message`, pass the incoming text through `user_message`, and keep its output in the named result variable. The model must contain `${ENV_VAR}` placeholders for URL, token, and model name; select the specific variables named by the user or task runtime. The MCP publisher resolves these placeholders only inside the outbound model configuration and redacts them from local artifacts. A `script` block runs sandboxed Python in `async def handler(context: Context) -> None:`. Imports are forbidden. Read and write named values through `context.session`; it needs `result_variable_name` and must store its final value in the corresponding session field. An `answer` that displays a computed result should use `{{session.variable_name}}` explicitly.

An LLM or script node that is part of a workflow must set `next_node_id` to the next existing node. Use an explicit node containing `extend` when the workflow crosses into another scenario; link the preceding node to it through `next_node_id`. A terminal answer node may use `next_node_id: null`.

For a task that needs a real external MCP data source, use an `agent` block rather than an HTTP wrapper around an `/mcp` endpoint. It needs the same portable model configuration as `llm`, plus `tools: {"mcp_servers": [{"url": "https://.../mcp"}]}`. Copy the documented server URL and tool semantics exactly into the task's agent prompt; do not invent source data or credentials. Route its named result to an answer or a guarded next step.

## Stateful routing and handoff

Use a short sandboxed `script` to derive named session state from the current message, then use `single_if` with `title`, `expression`, `code_type`, and a same-scenario `target_node_id` for a guarded route. Its target is validated by this skill. Use `go_operator` only after a user-facing handoff message; it is terminal. Put global `init` and `no_match` edges on the main scenario and send both to a router/processing node, not to a greeting-only node.
