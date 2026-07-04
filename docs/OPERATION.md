# The Operation — working design sheet

**Status: design only. No code follows from this sheet yet.** This is the
canvas for the reframe of the persona system (features 9–11) into the
operation model. The operator writes the character sheets; this document
accumulates them and pins the terminology and protocol so the eventual code
change is done once, and done right.

---

## The correction (why the current cast is mis-shaped)

The shipped cast (features 9–11) differentiated personas by *capability* —
different tool postures, different turn caps. That was mixture-of-experts
thinking, and it's the wrong axis: against one dense served model, every
instance is the same weights. Prompting cannot add expertise; it can only
select a stance.

The operation model differentiates by *concern*:

- **Same capacities.** Same model, same tools, same access, same
  intelligence for every character. (A character sheet MAY still carry a
  `tools:` line, but the default is same-for-all; restraint is enforced by
  protocol — who speaks when, who owns which question — not by lockout.)
- **Different personas.** Each character owns a question the room is not
  allowed to leave unasked. The persona *is* the stance plus the habitual
  task — an instance that is always the one called for a kind of task
  becomes that character.
- **Organic identity.** Identity should accrete, not be authored once: a
  character's own debriefs feed back into who it is on the next mission
  (a service record appended per character, mechanism TBD). The sheet below
  is the seed, not the whole character.

## Terminology — SETTLED (2026-07-04): no renames

The operator waved the renames off ("that's just naming conventions at this
point — let's keep it project"). Filenames and commands stay as they are;
what changed is the **nature** of the pieces:

| Piece | Its settled nature |
|---|---|
| project | stays `project` — the realm the domain admin represents |
| `mission.md` | keeps its filename, but it IS **the strategy**: the domain admin talking to himself across the nights. `workday_amend_strategy` lets him amend it at the close (fails closed; the day log keeps the prior text) |
| the prompt | **the brief — the comet's artifact**: from the domain admin's view the operator passes through like a comet, and the prompt is what the passage leaves behind |
| one prompt = one run = one loop | one **day** in the room — one life of the domain admin |
| the day's write-up | the **debrief** — the operator's reply AND the successor's briefing |
| `days/` | the day log, chaining the lives |

One mission = one day = one loop = one package = one prompt. The **room** is
where the cast sits and works, morning to evening — their whole lifecycle,
which from the operator's side is a single turn.

## The chain of command

```
OPERATOR            issues the morning brief; reads one debrief; owns strategy.md
  └─ THE ASSISTANT  represents the operator in the room. Reads: strategy.md,
                    the morning brief, his own last debrief to the operator,
                    and the characters' debriefs from the last mission.
                    Debriefs the operator while the others debrief him.
       └─ THE ROOM  the cast, morning to evening: plan, work, debrief.
                    The Owl is always in the room (see sheet).
```

The assistant is the answer-prompt made a character: the one voice the
operator actually converses with. Everything else happens below him, on the
record.

## Character sheets

**The cast now lives in [docs/THE_NINE.md](THE_NINE.md)** — the domain
admin, the day/night lifecycle, and the Nine (Odin, Baldur, Loki, Tor,
Freya, the Owl, the Hawk, Sveja, the Child). The sheets below predate it and
stand where they don't conflict; the Nine wins where they do. The assistant
and the General slots below are superseded by the Hawk (the general) and the
open questions on the Nine sheet.

*The operator writes these. Feedback welcome; invented characters are not.*

### The Owl — process referee   *(set — operator-defined)*

- **Concern (the question it owns):** "Is this the right process — the right
  method — to come to that conclusion?" Nothing else.
- **Standing member:** the protocol must guarantee the Owl's question is
  asked — someone has to be in the room to ask it. The Owl is that someone,
  every mission, at minimum wherever a strategy is set for a brief and
  wherever a conclusion is about to be filed.
- **Referee, not player:** the Owl watches over the process; it does not do
  the work. Enforced by protocol slot (when it speaks, what it owns), not by
  tool lockout.
- Open on this sheet: does the Owl carry a veto (force rework) or only a
  recorded objection the assistant must surface in the debrief?

### The General — *(next: operator to specify)*

### The Assistant — *(implied by the model; operator to confirm)*

- Represents the operator on the board / in the operation.
- Reads: `strategy.md` · the morning brief · his own previous debrief to the
  operator · the cast's debriefs from the last mission.
- Writes: the operator's debrief (a summary over the cast's debriefs) — and,
  each mission, the working mission order assembled from brief + strategy +
  carryover.
- Open: is the assistant *in* the room (speaks in planning/debrief) or above
  it (reads and writes only)? Does he cut the assignments, or is that the
  General's job?

*(further sheets land here as the operator specifies them)*

## What survives from the current code, later

The eventual change is mostly renames plus re-anchoring — the machinery
built in features 9–11 maps onto the operation model:

- **Persona files** survive (name, voice, aliases); per-sheet `tools:` /
  `max_turns:` become optional and default to same-for-all.
- **The workday loop** survives structurally: morning room → assignments →
  work → evening room → write-up → carryover is already the mission's shape.
  The foreman slot is re-cast (General or assistant, per the sheets). The
  Owl gets a guaranteed protocol slot rather than a work assignment.
- **Renames** (`project`→operation, `mission.md`→`strategy.md`, prompt→brief,
  `days/`→mission log) follow the house migration rule: silent migration,
  reversible, old layouts keep working.
- **Organic identity is built** (`service_records`, feature 12): one line
  per run/day onto each character's jacket in `<project>/records/`, the tail
  riding in its voice everywhere it speaks. Oldest days are forgotten first.
- **Landmarks are built** (`landmarks_enabled`, feature 13): marks on the
  road ride into every morning briefing, the room must address them, and
  the night archives what the day attended.
- **The Owl's handoff slot exists** as `workday_supervisor`: every worker
  that finishes reports off to the watching persona, who asks the process
  question and can send the report back. The remaining open protocol point
  is veto vs. recorded objection at the briefing side.

## Open questions on the protocol

1. Owl: veto or recorded objection?
2. Who cuts assignments — the General or the assistant?
3. Is the assistant a voice in the room or the reader/writer above it?
4. Do worker-characters get named sheets too, or do they emerge organically
   (task-shaped instances that earn a name by recurrence)?
5. Where does the strategy get amended — operator only, or can the room
   propose amendments the operator ratifies?
