# Iterative delivery loop

Use this platform-independent skill for any build/change task.

1. Inspect the current state before editing it.
2. Produce a draft, validate it structurally, then perform the requested operation.
3. Derive a coverage-driven black-box test plan from the requested behavior and exercise it through the real public interface. Decide the number of cases from the distinct observable requirements, branches, integrations, and safety behaviors in the task; do not use a fixed count. Give each case a concise name and avoid a generic hello-only check. When a behavior depends on earlier turns, put ordered steps in one shared-session case; otherwise use independent cases. Assert only behavior the user requested: do not require an arbitrary exact joke, generated wording, identifier, or other implementation detail when a non-empty response and the requested UI/state transition are the real contract.
4. Treat a failed check as feedback: inspect the error, repair the draft, and repeat only the necessary step.
5. Declare success only after the requested operation and the complete verification suite both succeed. Keep a concise progress journal.

Never encode benchmark examples, hidden tests, or domain-specific sample answers in this skill.
