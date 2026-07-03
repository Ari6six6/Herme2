"""Feature 9: the dynamic router. One cheap dispatcher call; every failure
mode falls OPEN to the default agent — routing may never block a run."""

import re

from hermes import personas as personas_mod
from hermes.llm import ChatResult, LLMTransportError

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


class OneShot:
    """A backend that answers a single routing call with a fixed reply."""

    def __init__(self, text):
        self.text = text
        self.seen = None

    def chat(self, messages, tools=None, tool_choice=None):
        self.seen = messages
        return ChatResult(content=self.text)


class Broken:
    def chat(self, messages, tools=None, tool_choice=None):
        raise LLMTransportError("endpoint down")


def _route(project, cfg, backend):
    catalog = personas_mod.load_all(project)
    return personas_mod.route("why is the tunnel flapping?", catalog, backend,
                              cfg, THINK_RE)


def test_route_picks_a_persona(project, cfg):
    p = _route(project, cfg, OneShot("The analyst fits.\nPERSONA: owl"))
    assert p is not None and p.name == "owl"


def test_route_none_verdict_means_default_agent(project, cfg):
    assert _route(project, cfg, OneShot("PERSONA: none")) is None


def test_route_strips_think_tags(project, cfg):
    reply = "<think>hmm PERSONA: smith is tempting</think>\nPERSONA: owl"
    p = _route(project, cfg, OneShot(reply))
    assert p.name == "owl"


def test_route_last_match_wins_when_menu_is_echoed(project, cfg):
    reply = "Candidates: PERSONA: smith? PERSONA: scout?\nFinal: PERSONA: owl"
    assert _route(project, cfg, OneShot(reply)).name == "owl"


def test_route_garbage_and_unknown_fall_back(project, cfg):
    assert _route(project, cfg, OneShot("I think the owl.")) is None
    assert _route(project, cfg, OneShot("PERSONA: minotaur")) is None


def test_route_transport_error_never_raises(project, cfg):
    assert _route(project, cfg, Broken()) is None


def test_route_empty_catalog_skips_the_call(project, cfg):
    backend = OneShot("PERSONA: owl")
    assert personas_mod.route("x", {}, backend, cfg, THINK_RE) is None
    assert backend.seen is None  # no LLM call was made


def test_router_call_carries_roster_and_request(project, cfg):
    backend = OneShot("PERSONA: none")
    catalog = personas_mod.load_all(project)
    personas_mod.route("audit the parser", catalog, backend, cfg, THINK_RE)
    sent = backend.seen[0]["content"]
    assert "`owl`" in sent and "`smith`" in sent  # the roster menu
    assert "audit the parser" in sent
    assert "{{" not in sent  # all placeholders rendered


def test_router_request_is_truncated(project, cfg):
    backend = OneShot("PERSONA: none")
    catalog = personas_mod.load_all(project)
    personas_mod.route("A" * 10000, catalog, backend, cfg, THINK_RE)
    sent = backend.seen[0]["content"]
    assert "A" * personas_mod.ROUTER_REQUEST_CHARS in sent
    assert "A" * (personas_mod.ROUTER_REQUEST_CHARS + 1) not in sent
