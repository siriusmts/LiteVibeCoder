# MWS no-code platform

This skill supplies the MWS-specific contract to the generic agent loop. It can be replaced as a unit by another directory containing this file and `platform.json`; set `MWS_AGENT_PLATFORM_SKILL` to that directory.

## Delivery flow

For a new bot: import the validated draft, publish the returned version, then send an engine message with a fresh session and message ID. For an existing bot: successfully inspect the selected bot through `inspect_existing_bot`, import a version, make it current, publish it, then test it. The MCP runtime rejects an update publication when that successful inspection has not occurred in the current run.

After the first successful publication in a create run, repairs are imported as new versions of that created bot rather than as separate bots. Verify every published version before publishing another repair; the loop rejects an additional publication while verification of the latest one is pending.

For a new project, use a portable lower-case `botName`. If the platform reports that it is already occupied, the runtime retries locally with a bounded unique suffix rather than spending another model turn or creating an unrelated project.

Report the frontend URL with `botVersionId` and the first returned scenario `id` as `activeScenarioId`. A version is not ready merely because import succeeds.

## Payload rules

Use the fields, routes, envelope, and validation rules in `platform.json`. They are machine-readable precisely so that platform details remain outside the generic loop. Do not put API keys or bearer tokens in skills, payloads, or logs.

## Interactive flows

For a menu or branching dialogue, use a `buttons` block with `buttons: [{"title": "…", "target_node_id": "…"}]`, followed by `wait_for_user`. A target must name a node in the same scenario. A node can answer, display another menu, or route to a subsequent node. This is a general interaction pattern; choose the actual menu items and content from the user's request. Do not implement a visible menu through `single_if`: it is for guarded routing after processing, not for a user-facing choice. A `single_if` block must use `code_type` `python` or `custom`, never `javascript`.

## Multiple scenarios

When a request needs multiple scenarios, give each scenario a distinct local integer `id`. Link them through an `extend` block using `scenario_id` equal to the target scenario's local id. The source node still needs a normal node id and block list. Do not use IDs from an unrelated, already-published bot.

## LLM and script blocks

An `llm` block needs `system_message`, `user_message`, `result_variable_name`, and `model`. Put the detailed classifier instruction in `system_message`, pass the incoming text through `user_message`, and keep its output in the named result variable. The model must contain `${ENV_VAR}` placeholders for URL, token, and model name; select the specific variables named by the user or task runtime. The MCP publisher resolves these placeholders only inside the outbound model configuration and redacts them from local artifacts. A `script` block runs sandboxed Python in `async def handler(context: Context) -> None:`. Imports are forbidden. Read and write named values through `context.session`; it needs `result_variable_name` and must store its final value in the corresponding session field. An `answer` that displays a computed result should use `{{session.variable_name}}` explicitly.

An LLM or script node that is part of a workflow must set `next_node_id` to the next existing node. Keep processing and presentation separate: put the `llm`, `agent`, or `script` block in a processing node, then route it to a result node that contains the computed `answer`, buttons, and optional `wait_for_user`. For independent branches, every processing node must point to its own result node; node array order has no routing meaning and must never be used to chain unrelated branches. Use an explicit node containing `extend` when the workflow crosses into another scenario; link the preceding node to it through `next_node_id`. A terminal answer node may use `next_node_id: null`.

## External integrations

For a documented REST endpoint, use the native `http_request` block. It is the platform's general integration block: provide `url`, an HTTP `method`, optional `headers`, `body`, `timeout`, and `retry_attempts_count`; interpolate runtime variables in URL, headers, or body as `{{system.last_user_message}}` or another named variable. Map response fields into context with `response_mapping: [{"key":"session.result_field","value":"$response.body.<path>"}]`; every mapping key must explicitly begin with `session.`. Then route `ok_target_node_id` to a result node and `error_target_node_id` to a user-facing error/retry node. Use an array index in the response path when the documented API returns a list. This is appropriate for any ordinary REST integration, not only a particular service. Platform templates always use `{{scope.variable}}`; never use shell/JavaScript syntax `${variable}`.

The generic graph shape is: an input node (`answer`, `wait_for_user`, then node-level `next_node_id`) → request node (`http_request` with success/error node targets) → result or error node (`answer`, optional `buttons`, `wait_for_user`). Put `next_node_id` only on a node, never inside a block. The only menu block type is `buttons`; do not invent an `interactive` block. A minimal request block is `{ "type":"http_request", "url":"https://service.example/search?q={{system.last_user_message}}", "method":"GET", "response_mapping":[{"key":"session.result_title","value":"$response.body.items[0].title"}], "ok_target_node_id":"show_result", "error_target_node_id":"show_error" }`.

Response mappings are exposed as lists, including a mapping that selects one JSON value. For a possibly empty API result, first route with a short condition such as `session.result_title` (truthiness) to the success node; leave the following blocks in that node as the empty-result path. Do not index `[0]` in that condition: an empty list makes the engine fail. The condition DSL does not accept natural-language forms such as `is not empty`; use the scoped value itself. In the success answer, render the scalar with `{{session.result_title[0]}}`. Map all fields needed for display directly and do not add a script merely to unpack the JSON response. This makes the same graph handle a populated list and an empty response without a script.

Scripts have no outbound HTTP capability. A raw REST URL is not an MCP server and must not be embedded in a script or passed as an MCP server URL. Use an `agent` block only when the user or runtime supplies a real, reachable MCP endpoint with documented tools. If the requested external capability has neither a native platform integration nor such an MCP endpoint, report the missing capability instead of publishing a simulated integration. Prefer `response_mapping` over a script when merely extracting fields from an HTTP JSON response.

When a script is genuinely needed, the platform passes a `ContextAccessor`, not an ordinary Python dictionary. Use direct static attributes only: `context.session.field`, `context.system.field`, `context.temp.field`, or `context.memory.field`; assign results the same way. Never use `context.get(...)`, square-bracket access, or `.get(...)` on a context scope. Keep scripts small and use them only for local state/routing, not as an integration layer.

For a task that needs a real external MCP data source, use an `agent` block rather than an HTTP wrapper around an `/mcp` endpoint. It needs the same portable model configuration as `llm`, plus `tools: {"mcp_servers": [{"url": "https://.../mcp"}]}`. Copy the documented server URL and tool semantics exactly into the task's agent prompt; do not invent source data or credentials. Route its named result to an answer or a guarded next step.

## Stateful routing and handoff

Use a short sandboxed `script` to derive named session state from the current message, then use `single_if` with `title`, `expression`, `code_type`, and a same-scenario `target_node_id` for a guarded route. `single_if.expression` is platform DSL, not arbitrary Python: refer to scoped values directly (for example `session.result`), use `null` and the documented comparison operators, and never index a `context[...]` object or call Python functions such as `len()`. Its target is validated by this skill. Use `go_operator` only after a user-facing handoff message; it is terminal. Put global `init` and `no_match` edges on the main scenario and send both to a router/processing node, not to a greeting-only node.
