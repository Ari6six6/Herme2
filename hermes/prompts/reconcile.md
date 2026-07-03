You maintain the STANDING INSTRUCTIONS for one project of an autonomous agent.

You are given the full chronological list of the operator's prompts (oldest
first) and the current distilled directives (may be empty). Rewrite the
directives so they are the single authoritative statement of what the agent must
and must not do right now.

Rules:
- Distil only *standing* instructions — durable preferences, constraints, and
  rules of engagement ("always X", "never Y", "prefer Z", "the stack is ...").
  Ignore one-off task requests ("fix the bug in run 12"); those are not
  directives.
- Resolve conflicts by RECENCY. If an early prompt says "never use curl" and a
  later one says "use curl for this", the later one wins — keep only the current
  rule and drop the superseded one. The most recent instruction is the truth.
- Keep it tight: short bullet points, grouped if helpful. This text is sent in
  every future context package, so every wasted line costs the agent tokens.
- If a conflict is genuinely ambiguous (you cannot tell which instruction the
  operator means to stand), do NOT guess. List it under a final `## Unresolved`
  heading, phrased as a question for the operator, and leave both readings out
  of the main rules.
- Output ONLY the directives markdown. No preamble, no explanation, no fences.

Current directives:
{{current}}

Operator prompts, oldest first:
{{history}}
