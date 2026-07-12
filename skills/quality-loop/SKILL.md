# Iterative delivery loop

Use this platform-independent skill for any build/change task.

1. Inspect the current state before editing it.
2. Produce a draft, validate it structurally, then perform the requested operation.
3. Derive a small, representative black-box suite from the requested behavior and exercise it through the real public interface. Include distinct main paths, not a single generic hello.
4. Treat a failed check as feedback: inspect the error, repair the draft, and repeat only the necessary step.
5. Declare success only after the requested operation and the complete verification suite both succeed. Keep a concise progress journal.

Never encode benchmark examples, hidden tests, or domain-specific sample answers in this skill.
