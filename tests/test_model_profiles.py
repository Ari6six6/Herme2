"""Per-model build profiles: each added model carries its own tuned sampling,
completion budget, stall tolerance, reasoning tags, and tool-call discipline —
while the baseline (Hermes) profile equals the app defaults, so the base path
Fable 5 designed is left byte-for-byte unchanged.
"""

import httpx

from hermes import agent, models, package
from hermes.config import DEFAULTS
from hermes.llm import OpenAIBackend
from tests.test_agent_loop import run_agent


# --------------------------------------------------------------- the catalog
def test_hermes_profile_equals_app_defaults():
    h = models.HERMES
    assert h.sampling == DEFAULTS["sampling"]
    assert h.max_completion_tokens == DEFAULTS["max_completion_tokens"]
    assert h.stall_nudges == DEFAULTS["stall_nudges"]
    assert h.think_tags == ("think", "seed:think")
    assert h.tool_guidance == ""
    assert h.supports_forced_tool_choice is True


def test_every_added_model_has_a_distinct_tuned_build():
    base = models.HERMES
    for spec in (models.QWEN, models.QWEN_OFFICIAL, models.QWEN_40B):
        assert spec.tool_guidance.strip(), f"{spec.key} needs tool guidance"
        # something about its build differs from the Hermes baseline
        assert (
            spec.sampling != base.sampling
            or spec.max_completion_tokens != base.max_completion_tokens
            or spec.stall_nudges != base.stall_nudges
        ), f"{spec.key} is not actually tuned"


def test_gguf_models_skip_forced_tool_choice():
    # llama.cpp under --jinja doesn't honour named tool_choice
    assert models.QWEN.supports_forced_tool_choice is False
    assert models.QWEN_40B.supports_forced_tool_choice is False
    assert models.QWEN_OFFICIAL.supports_forced_tool_choice is True  # vLLM


def test_thinking_models_get_more_completion_headroom():
    assert models.QWEN_OFFICIAL.max_completion_tokens > models.HERMES.max_completion_tokens
    assert models.QWEN_40B.max_completion_tokens > models.HERMES.max_completion_tokens


def test_runtime_config_round_trips_into_config(cfg):
    spec = models.QWEN_40B
    for key, value in spec.runtime_config().items():
        cfg.set(key, value)
    assert cfg.get("sampling") == spec.sampling
    assert cfg.get("max_completion_tokens") == spec.max_completion_tokens
    assert cfg.get("stall_nudges") == spec.stall_nudges


# ------------------------------------------------------------------- client
def _capture_backend(captured, sampling):
    def handler(request):
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    base = {"base_url": "http://x/v1", "api_key": "k", "model": "m",
            "sampling": sampling, "max_completion_tokens": 8192}

    class DictCfg:
        def get(self, key, default=None):
            return base.get(key, default)

    b = OpenAIBackend(DictCfg())
    b.client = httpx.Client(transport=httpx.MockTransport(handler))
    return b


def test_baseline_sampling_sends_no_extra_knobs():
    captured = {}
    b = _capture_backend(captured, models.HERMES.sampling)
    b.chat([{"role": "user", "content": "hi"}])
    for knob in ("min_p", "presence_penalty", "frequency_penalty", "repetition_penalty"):
        assert knob not in captured["body"]


def test_tuned_sampling_forwards_extra_knobs():
    captured = {}
    b = _capture_backend(captured, models.QWEN.sampling)
    b.chat([{"role": "user", "content": "hi"}])
    assert captured["body"]["min_p"] == 0.05
    assert captured["body"]["presence_penalty"] == 0.6
    assert captured["body"]["temperature"] == 0.5


# -------------------------------------------------------------- think tags
def test_per_model_think_tags_stripped():
    qwen_re = agent._think_re(models.QWEN.think_tags)
    assert agent.strip_think("<thinking>x</thinking>answer", qwen_re) == "answer"
    assert agent.strip_think("<think>y</think>done", qwen_re) == "done"
    # default (Hermes) pattern still handles seed:think
    assert agent.strip_think("<seed:think>z</seed:think>ok") == "ok"


# ---------------------------------------------------------- system prompt
def test_tool_guidance_injected_only_when_present(project, cfg):
    plain = package.build_system_prompt(project, {})
    assert "Operating notes for this model" not in plain
    tuned = package.build_system_prompt(
        project, {"model_tool_guidance": models.QWEN.tool_guidance}
    )
    assert "Operating notes for this model" in tuned
    assert "Act, don't narrate" in tuned


# ----------------------------------------------- forced-choice guard (loop)
def test_gguf_model_does_not_force_tool_choice(project, cfg):
    """With a GGUF model, a prose-only run can't be rescued by a forced
    finish_run (the runtime won't honour it), so it lands on a stub summary
    rather than the MockBackend's forced '[mock] run done.'"""
    cfg.set("model_id", "qwen-40b")
    cfg.set("stall_nudges", 0)
    result = run_agent(project, cfg, [{"text": "all done"}])
    assert result.final_text == "all done"
    assert "[auto-stub" in result.summary


def test_baseline_model_still_forces_tool_choice(project, cfg):
    cfg.set("model_id", "hermes")
    cfg.set("stall_nudges", 0)
    result = run_agent(project, cfg, [{"text": "all done"}])
    assert result.summary == "[mock] run done."
