# Vibecoding agent skill index

The generic runtime loads detachable skills rather than embedding platform knowledge in prompts or code.

- `skills/quality-loop/SKILL.md` — reusable iterative validation and delivery method.
- `skills/mws-nocode/` — MWS API instructions plus machine-readable `platform.json` for routes, validation, headers, envelopes, and frontend links.

To replace the platform integration without changing the core loop, point `MWS_AGENT_PLATFORM_SKILL` to a compatible skill directory. To replace the work style, set `MWS_AGENT_WORK_STYLE_SKILL` to another `SKILL.md`.
