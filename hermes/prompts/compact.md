You are compacting the MIDDLE of an agent's live working session to save
context. Below is a verbatim slice of that session (the agent's messages, its
tool calls, and the tool results). Summarize it into a compact briefing the
agent can rely on to keep working, as if it had done this itself.

Preserve — verbatim, do not paraphrase or round:
- Decisions made and why.
- Every file created or modified, by exact path.
- Exact commands that were run (the literal command line).
- Exact error messages and failure output (copy the real strings — flags,
  tracebacks, exit codes, the lot; a paraphrased error is useless later).
- Open threads: what's still unfinished, what was about to happen next.

Drop: narration, repeated reasoning, anything already superseded.

Output plain text, tight. No preamble. This replaces the slice, so anything you
omit is gone.

--- SESSION SLICE ---
{{slice}}
--- END SLICE ---
