# MWS no-code platform

This skill supplies the MWS-specific contract to the generic agent loop. It can be replaced as a unit by another directory containing this file and `platform.json`; set `MWS_AGENT_PLATFORM_SKILL` to that directory.

## Delivery flow

For a new bot: import the validated draft, publish the returned version, then send an engine message with a fresh session and message ID. For an existing bot: inspect it, import a version, make it current, publish it, then test it.

Report the frontend URL with `botVersionId` and the first returned scenario `id` as `activeScenarioId`. A version is not ready merely because import succeeds.

## Payload rules

Use the fields, routes, envelope, and validation rules in `platform.json`. They are machine-readable precisely so that platform details remain outside the generic loop. Do not put API keys or bearer tokens in skills, payloads, or logs.

## Interactive flows

For a menu or branching dialogue, use a `buttons` block with `buttons: [{"title": "…", "target_node_id": "…"}]`, followed by `wait_for_user`. A target must name a node in the same scenario. A node can answer, display another menu, or route to a subsequent node. This is a general interaction pattern; choose the actual menu items and content from the user's request.

## Multiple scenarios

When a request needs multiple scenarios, give each scenario a distinct local integer `id`. Link them through an `extend` block using `scenario_id` equal to the target scenario's local id. The source node still needs a normal node id and block list. Do not use IDs from an unrelated, already-published bot.

## LLM and script blocks

An `llm` block needs `system_message`, `user_message`, `result_variable_name`, and `model`. Put the detailed classifier instruction in `system_message`, pass the incoming text through `user_message`, and keep its output in the named result variable. The model must contain platform placeholders for URL, token, and model name. A `script` block needs executable `value` and `result_variable_name`; it should consume named variables rather than relying on hidden state. An `answer` that displays a computed result should refer to its result variable explicitly.
