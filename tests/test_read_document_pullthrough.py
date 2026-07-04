"""Proof that the document-reader capability closes the loop.

With skills on and the shipped `read_document` seed skill in place, a natural
prompt drives: load_skill -> equip_tool -> extractor runs -> answer. The
transcript and metrics.json are the evidence (the same artifacts the operator
and the retrospection pass read).

Wiring proof (scripted MockBackend): it shows the harness routes the
skill+tool pull-through, not that a live model would choose it.
"""

import json
from pathlib import Path

import pytest

from hermes import agent
from hermes.llm import MockBackend

SEED = Path(__file__).resolve().parents[1] / "skills" / "read_document.md"


def _install_seed(project):
    project.skills_dir.mkdir(parents=True, exist_ok=True)
    (project.skills_dir / "read_document.md").write_text(SEED.read_text())


def _transcript(project):
    return (project.runs_dir / "0001" / "transcript.jsonl").read_text()


def test_html_reader_pulls_through(project, cfg):
    cfg.set("skills_enabled", True)
    _install_seed(project)
    # As if download_file had already saved the page to the workspace.
    (project.workspace_dir / "report.html").write_text(
        "<html><body><h1>Q3 Report</h1>"
        "<script>track()</script>"
        "<p>Revenue grew <b>18%</b> year over year.</p></body></html>"
    )

    backend = MockBackend([
        {"tool": "load_skill", "args": {"name": "read_document"}},
        {"tool": "equip_tool", "args": {"name": "html_to_text"}},
        {"tool": "html_to_text", "args": {"src": "workspace/report.html"}},
        {"tool": "finish_run",
         "args": {"summary": "Read report.html: revenue grew 18% YoY."},
         "say": "The report says revenue grew 18% year over year."},
    ])
    result = agent.run(
        project, "read the report I downloaded and tell me the revenue growth",
        cfg, backend, gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )

    assert not result.aborted
    assert "18%" in result.summary
    tx = _transcript(project)
    assert "load_skill" in tx
    assert "html_to_text" in tx
    assert "Revenue grew 18% year over year." in tx  # extractor output, script gone
    assert "track()" not in tx                        # <script> was dropped

    metrics = json.loads((project.runs_dir / "0001" / "metrics.json").read_text())
    assert metrics["aborted"] is False
    assert metrics["tool_errors"] == 0
    assert "html_to_text" in metrics["tools"]
    assert "load_skill" in metrics["tools"]


def _pypdf_ok():
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pypdf_ok(), reason="pypdf not importable")
def test_pdf_reader_pulls_through(project, cfg):
    from test_pdf_text import _make_pdf  # reuse the in-process PDF builder

    cfg.set("skills_enabled", True)
    _install_seed(project)
    (project.workspace_dir / "paper.pdf").write_bytes(_make_pdf(["Findings: it works"]))

    backend = MockBackend([
        {"tool": "load_skill", "args": {"name": "read_document"}},
        {"tool": "equip_tool", "args": {"name": "pdf_text"}},
        {"tool": "pdf_text", "args": {"src": "workspace/paper.pdf"}},
        {"tool": "finish_run",
         "args": {"summary": "Read paper.pdf: the findings say it works."}},
    ])
    result = agent.run(
        project, "summarise the pdf I saved to the workspace",
        cfg, backend, gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )
    assert not result.aborted
    tx = _transcript(project)
    assert "pdf_text" in tx
    assert "Findings: it works" in tx  # real extracted text in the transcript
    metrics = json.loads((project.runs_dir / "0001" / "metrics.json").read_text())
    assert metrics["tool_errors"] == 0
    assert "pdf_text" in metrics["tools"]
