"""Feature 5: prefix-cache-friendly package ordering."""

from hermes import package


def _text(msgs):
    return msgs[0]["content"] + "\n\x1e\n" + msgs[1]["content"]


def _common_prefix(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


ENV_A = {"gpu_status": "1.1.1.1:8000 (vllm:up)", "managed_hosts": "none",
         "context_window": 60000}
ENV_B = {"gpu_status": "2.2.2.2:8000 (vllm:DOWN)", "managed_hosts": "web=root@2.2.2.2",
         "context_window": 60000}


def test_off_status_in_header(project, cfg):
    cfg.set("prefix_cache_order", False)
    system = package.assemble(project, "x", ENV_A, cfg)[0]["content"]
    assert "GPU: 1.1.1.1:8000" in system  # volatile status inline in the header
    assert "# RUNTIME STATUS" not in package.assemble(project, "x", ENV_A, cfg)[1]["content"]


def test_off_header_diverges_on_status_change(project, cfg):
    # With ordering off, a GPU/host change perturbs the header early.
    cfg.set("prefix_cache_order", False)
    sa = package.assemble(project, "x", ENV_A, cfg)[0]["content"]
    sb = package.assemble(project, "x", ENV_B, cfg)[0]["content"]
    assert sa != sb


def test_on_header_is_stable_across_status_change(project, cfg):
    cfg.set("prefix_cache_order", True)
    sa = package.assemble(project, "req one", ENV_A, cfg)[0]["content"]
    sb = package.assemble(project, "different req two", ENV_B, cfg)[0]["content"]
    # the system prompt is byte-identical despite the changed GPU/host and request
    assert sa == sb
    assert "GPU:" not in sa  # volatile status is gone from the header
    # ...and it moved into the user message
    ua = package.assemble(project, "req one", ENV_A, cfg)[1]["content"]
    assert "# RUNTIME STATUS" in ua
    assert "GPU: 1.1.1.1:8000" in ua


def test_on_shared_prefix_covers_full_header(project, cfg):
    # Put skills in the header too, so we prove the whole stable block is shared.
    from hermes import skills as skills_mod
    cfg.set("prefix_cache_order", True)
    cfg.set("skills_enabled", True)
    skills_mod.write(project, "s1", "a one-liner\n\nbody", scope="global")
    a = _text(package.assemble(project, "probe one", ENV_A, cfg))
    b = _text(package.assemble(project, "a different probe two", ENV_B, cfg))
    shared = _common_prefix(a, b)
    system_len = len(package.assemble(project, "probe one", ENV_A, cfg)[0]["content"])
    # the shared prefix spans at least the entire system prompt (header + persona
    # + tools catalog + skills index)
    assert shared >= system_len
    assert "## Skills" in a[:system_len]


def test_on_prefix_beats_off_when_status_changes(project, cfg):
    # Same two volatile-differing calls: ordering ON must share a longer prefix.
    cfg.set("prefix_cache_order", False)
    off_a = _text(package.assemble(project, "p1", ENV_A, cfg))
    off_b = _text(package.assemble(project, "p1", ENV_B, cfg))
    off_shared = _common_prefix(off_a, off_b)
    cfg.set("prefix_cache_order", True)
    on_a = _text(package.assemble(project, "p1", ENV_A, cfg))
    on_b = _text(package.assemble(project, "p1", ENV_B, cfg))
    on_shared = _common_prefix(on_a, on_b)
    assert on_shared > off_shared
