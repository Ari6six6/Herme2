You are reviewing your own recent performance in this project — not to do new
work, but to make future runs go better. This is a private reflection pass:
nothing here changes any answer already given to the operator.

Below: per-run METRICS the harness recorded, your own RUN SUMMARIES, your
current SKILLS INDEX, and the tail of your NOTES. The metrics are ground truth
— the harness counted them while running the loop; you did not author them. Do
not argue with them and do not embellish them.

# METRICS (harness-recorded, oldest first)

{{metrics}}

Reading the columns: `aborted` = hit the turn cap or the error breaker.
`tool_errors` = tool calls that came back ERROR/DENIED. `stall_nudges` =
bounced for narrating instead of acting. `phantom_bounces` = tried to finish
with code only pasted in the reply. `verify_bounces` / `verify_failures` =
finished without verifying / failed independent verification. `tainted_turns`
= turns gated because untrusted network content was in scope.

# RUN SUMMARIES

{{summaries}}

# SKILLS INDEX

{{skills_index}}

# NOTES (tail)

{{notes}}

Look for what RECURS: the same tool erroring run after run, aborts on the same
kind of task, repeated bounces for the same bad habit. For each recurring
pattern where you can state a concrete fix:

- `write_skill` a procedure (or update the existing one — writing the same
  name edits it in place) when the fix is a how-to: the exact commands, the
  error you keep hitting, the way past it. (Only when the skill tools are
  available to you in this pass.)
- `write_note` when the fix is a fact or standing reminder your future
  packages should carry.

Rules: every conclusion must trace to the data above — no invented problems,
no self-congratulation. One or two high-value writes beat many. You cannot
touch mission, persona, or directives — those belong to the operator. If
nothing recurs, say "nothing worth changing" and stop.
