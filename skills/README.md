# Seed skills

Canonical, version-controlled copies of skills that pair with shipped toolbox
tools. Skills are the agent's own how-to notes (see `docs/USAGE.md` — Feature 3);
at runtime they live in `~/.hermes/skills/` (global) or `<project>/skills/`
(per-project) and only their one-line index rides in the prompt, with
`load_skill(name)` pulling the full body on demand.

These files are the source of truth for the skills that ship alongside a
capability. To make one available to the agent, copy it into your global skills
dir:

```sh
mkdir -p ~/.hermes/skills
cp skills/git_ops.md ~/.hermes/skills/
```

Each file's first line is its one-line description; the rest is the exact
procedure plus the gotchas. Edit them by hand (`nano`) like any other skill.
