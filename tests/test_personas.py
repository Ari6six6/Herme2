"""Feature 9: personas — the catalog, invocation parsing, and seam A
(a top-level run adopting a persona's voice, tools and turn cap)."""

import json

from hermes import agent, package
from hermes import personas as personas_mod
from hermes.llm import ChatResult, ToolCall


# ---- header parsing -----------------------------------------------------------
def test_parse_full_header():
    p = personas_mod.parse("owl", (
        "# the Analyst — dissects problems\n"
        "tools: read_file, list_files\n"
        "aliases: the-owl, analyst\n"
        "max_turns: 14\n"
        "\n"
        "You are the Owl.\n"
    ), "builtin", None)
    assert p.description == "the Analyst — dissects problems"
    assert p.tools == ["read_file", "list_files"]
    assert p.aliases == ["the-owl", "analyst"]
    assert p.max_turns == 14
    assert p.voice == "You are the Owl."


def test_unknown_key_line_starts_the_body():
    # `Note:` looks like a header but isn't a known key — it must land in the
    # voice, never be silently eaten.
    p = personas_mod.parse("x", (
        "capacity line\n"
        "Note: this is prose, not a header\n"
        "tools: never_parsed\n"
    ), "global", None)
    assert p.tools is None
    assert "Note: this is prose" in p.voice
    assert "tools: never_parsed" in p.voice  # part of the body once header ended


def test_headerless_file_gets_defaults():
    p = personas_mod.parse("x", "# just a capacity\n\nthe voice\n", "global", None)
    assert p.tools is None and p.aliases == [] and p.max_turns is None
    assert p.voice == "the voice"


def test_blank_line_after_description_ends_header():
    p = personas_mod.parse("x", "capacity\n\ntools: not, a, header\n", "global", None)
    assert p.tools is None
    assert "tools: not, a, header" in p.voice


def test_malformed_max_turns_ignored():
    p = personas_mod.parse("x", "cap\nmax_turns: soon\n\nvoice\n", "global", None)
    assert p.max_turns is None


def test_voice_truncated_at_max_chars():
    p = personas_mod.parse("x", "cap\n\n" + "V" * 5000, "global", None, max_chars=100)
    assert len(p.voice) <= 100 + len("\n[persona truncated]")
    assert p.voice.endswith("[persona truncated]")


# ---- catalog: scopes and shadowing ---------------------------------------------
def test_builtin_cast_ships_the_nine(project, cfg):
    cast = personas_mod.load_all(project)
    for name in ("odin", "baldur", "loki", "tor", "freya",
                 "owl", "hawk", "sveja", "arthur"):
        assert name in cast
        assert cast[name].scope == "builtin"
        # same capacities: the Nine differ by persona, never by tool posture
        assert cast[name].tools is None
        assert cast[name].max_turns is None


def test_global_shadows_builtin_and_project_shadows_global(project, cfg):
    gdir = personas_mod.global_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "owl.md").write_text("my own owl\n\nglobal owl voice\n")
    cast = personas_mod.load_all(project)
    assert cast["owl"].scope == "global"
    assert cast["owl"].voice == "global owl voice"

    project.personas_dir.mkdir(parents=True, exist_ok=True)
    (project.personas_dir / "owl.md").write_text("project owl\n\nproject owl voice\n")
    cast = personas_mod.load_all(project)
    assert cast["owl"].scope == "project"
    assert cast["owl"].voice == "project owl voice"


def test_bad_names_skipped(project, cfg):
    gdir = personas_mod.global_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "bad name!.md").write_text("nope\n\nx\n")
    assert "bad name!" not in personas_mod.load_all(project)


def test_alias_lookup_and_name_beats_alias(project, cfg):
    cast = personas_mod.load_all(project)
    assert personas_mod.resolve(cast, "the-owl").name == "owl"
    assert personas_mod.resolve(cast, "OWL").name == "owl"  # case-insensitive
    # a persona whose ALIAS collides with another persona's NAME must lose
    gdir = personas_mod.global_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "rook.md").write_text("cap\naliases: owl\n\nvoice\n")
    cast = personas_mod.load_all(project)
    assert personas_mod.resolve(cast, "owl").name == "owl"
    assert personas_mod.resolve(cast, "rook").name == "rook"


def test_index_is_cheap_and_bodyless(project, cfg):
    gdir = personas_mod.global_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (gdir / f"p{i}.md").write_text(
            f"does useful thing number {i} in one line\n\n" + "VOICE " * 300
        )
    cast = {n: p for n, p in personas_mod.load_all(project).items()
            if n.startswith("p")}
    idx = personas_mod.index(catalog=cast)
    assert idx.count("\n") == 5  # 6 lines
    assert len(idx) // 4 < 300  # ~4 chars/token
    assert "VOICE VOICE" not in idx


# ---- invocation parsing ---------------------------------------------------------
def _catalog(project):
    return personas_mod.load_all(project)


def test_hey_comma_form(project, cfg):
    p, rest, name = personas_mod.parse_invocation(
        "hey owl, why is the tunnel flapping?", _catalog(project))
    assert p.name == "owl" and rest == "why is the tunnel flapping?"


def test_hey_bare_and_at_forms(project, cfg):
    p, rest, _ = personas_mod.parse_invocation("hey owl look around", _catalog(project))
    assert p.name == "owl" and rest == "look around"
    p, rest, _ = personas_mod.parse_invocation("@owl look around", _catalog(project))
    assert p.name == "owl" and rest == "look around"


def test_hey_is_case_insensitive_and_takes_aliases(project, cfg):
    p, _, _ = personas_mod.parse_invocation("Hey The-Owl, check it", _catalog(project))
    assert p.name == "owl"


def test_typo_passes_whole_prompt_through(project, cfg):
    text = "hey olw why is it broken"
    p, rest, attempted = personas_mod.parse_invocation(text, _catalog(project))
    assert p is None
    assert rest == text  # the operator's words are never eaten
    assert attempted == "olw"


def test_plain_prompt_passthrough(project, cfg):
    text = "fix the parser in workspace/scraper.py"
    p, rest, attempted = personas_mod.parse_invocation(text, _catalog(project))
    assert p is None and rest == text and attempted is None


# ---- registry narrowing ---------------------------------------------------------
def test_filter_registry_is_strict_subset(project, cfg):
    from hermes.tools import build_registry
    reg = build_registry(project, cfg, lambda *a, **k: True)
    p = personas_mod.parse("x", "cap\ntools: read_file, not_a_real_tool\n\nv\n",
                           "global", None)
    narrowed = personas_mod.filter_registry(reg, p)
    assert "read_file" in narrowed.names()
    assert "not_a_real_tool" not in narrowed.names()
    assert "finish_run" in narrowed.names()  # always able to finish
    assert "write_file" not in narrowed.names()  # outside the posture
    assert set(narrowed.names()) <= set(reg.names())


def test_filter_registry_none_means_no_narrowing(project, cfg):
    from hermes.tools import build_registry
    reg = build_registry(project, cfg, lambda *a, **k: True)
    p = personas_mod.parse("x", "cap\n\nvoice\n", "global", None)
    assert personas_mod.filter_registry(reg, p) is reg


# ---- seam A: a run adopting a persona -------------------------------------------
class ScriptBackend:
    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, tool_choice=None):
        if self.turns:
            return self.turns.pop(0)()
        return ChatResult(content="(script exhausted)")


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


def test_persona_block_replaces_legacy_persona(project, cfg):
    p = personas_mod.get(project, "owl")
    system = package.build_system_prompt(project, {}, cfg, persona=p)
    assert "## Persona — owl" in system
    assert "You are the Owl" in system


def test_system_prompt_byte_identical_when_personas_off(project, cfg):
    # Reversibility: the toggle off must be provably the prior behaviour —
    # this also protects the prefix cache.
    baseline = package.build_system_prompt(project, {}, cfg)
    cfg.set("personas_enabled", True)
    assert package.build_system_prompt(project, {}, cfg) == baseline


def test_run_as_persona_logs_and_narrows(project, cfg):
    # A persona MAY still carry a tools line (an operator's own sheet); the
    # narrowing seam is exercised with a custom one — the Nine carry none.
    cfg.set("personas_enabled", True)
    gdir = personas_mod.global_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "quill.md").write_text(
        "files only\ntools: read_file, write_file, list_files, write_note\n\n"
        "You are Quill.\n")
    p = personas_mod.get(project, "quill")  # files only — no shell
    backend = ScriptBackend([
        _call("local_shell", {"command": "echo hi"}),  # outside the posture
        _call("finish_run", {"summary": "done as quill"}),
    ])
    result = agent.run(project, "tidy the notes", cfg, backend,
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True, persona=p)
    assert result.summary == "done as quill"
    lines = [json.loads(l) for l in
             (project.runs_dir / "0001" / "transcript.jsonl").read_text().splitlines()]
    assert any(e.get("role") == "persona" and e.get("content") == "quill"
               for e in lines)
    system = next(e for e in lines if e.get("role") == "system")
    assert "## Persona — quill" in system["content"]
    shell_result = next(e for e in lines
                        if e.get("role") == "tool" and "local_shell" in
                        e.get("content", ""))
    assert shell_result["content"].startswith("ERROR: unknown tool")


def test_persona_max_turns_caps_the_run(project, cfg):
    cfg.set("personas_enabled", True)
    gdir = personas_mod.global_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "brisk.md").write_text("cap\nmax_turns: 2\n\nvoice\n")
    p = personas_mod.get(project, "brisk")
    backend = ScriptBackend([
        _call("write_note", {"text": "turn 1"}),
        _call("write_note", {"text": "turn 2"}),
        _call("write_note", {"text": "turn 3 — must never run"}),
    ])
    result = agent.run(project, "go", cfg, backend,
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True, persona=p)
    assert result.turns == 2
    assert "turn 3" not in project.read_notes()
