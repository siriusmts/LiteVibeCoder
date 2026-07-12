# MWS no-code bot platform

Use this skill when creating, editing, validating, publishing, or testing a bot on MWS AI Platform.

## Safe workflow

1. For an existing bot, read its current state before proposing changes.
2. Build one complete draft with a clear bot name, fallback response, and at least one reachable scenario.
3. Validate the draft locally before any write. Keep a local payload snapshot.
4. Create with `POST /api/v3/nocode/bots/import/`; update with `POST /api/v3/nocode/bots/{botId}/import-version/`, then make the returned version current.
5. Test a published version through `POST /api/v3/nocode/bots/{botId}/bot-versions/{versionId}/engine/` using a fresh session and message id.
6. Report the platform status and frontend URL. Never report a draft or failed request as published.

## Minimal portable rules

- Send imports as `{"data":{"type":"bots","attributes": BOT}}`.
- `BOT` needs `botName`, `requestTtlInSeconds`, `noMatchStubAnswer`, `needPreprocess`, and nonempty `scenarios`.
- Use a unique `botName` containing only lowercase ASCII letters, digits, and underscores.
- Every scenario needs a name, entry edges, nodes, unique node ids, and blocks.
- Use `Accept`, `X-Ai-Workspace`, a fresh `request-id`, optional `X-Ai-Account`, and bearer authorization when a token is supplied.

## Guardrails

- Begin in dry-run mode when platform credentials or the desired change are not confirmed.
- Keep API keys and bearer tokens in environment variables only; never include them in payloads, logs, source, or documentation.
- Treat platform schema/API errors as feedback: inspect, repair the draft, revalidate, then retry deliberately.
