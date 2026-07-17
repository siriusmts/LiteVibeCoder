# Iterative delivery loop

Use this platform-independent skill for any build/change task.

1. Inspect the current state before editing it.
2. Produce a draft, validate it structurally, then perform the requested operation.
3. Derive a coverage-driven black-box test plan from the requested behavior and exercise it through the real public interface. Decide the number of cases from the distinct observable requirements, branches, integrations, and safety behaviors in the task; do not use a fixed count. Give each independently selectable named branch its own requirement instead of grouping sibling branches under one check. Test sibling branches in clean sessions to isolate their behavior, and also use one shared session when the request requires continued dialogue, switching, or state preservation. If an initial interaction prepares the session or asks for input, include it before testing a later interaction. When a response displays buttons, continue with an exact displayed label; do not paraphrase a button click. Before reporting a technical failure, reproduce the same path from a clean session and retain the sent message, session, visible response, and engine errors as evidence. Assert only behavior the user requested: do not require an arbitrary exact generated wording, identifier, or other implementation detail when a non-empty response and the requested UI/state transition are the real contract.
4. Treat a failed check as feedback: inspect the error, repair the draft, and repeat only the necessary step.
5. Declare success only after the requested operation and the complete verification suite both succeed. Keep a concise progress journal.

Never encode benchmark examples, hidden tests, or domain-specific sample answers in this skill.
