"""html_to_text: HTML -> readable text, local only (no fetching)."""

from hermes.toolbox import html_to_text
from hermes.tools.base import ToolContext


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


PAGE = """<html><head><title>Ignore me</title><style>.a{color:red}</style></head>
<body><h1>Title</h1>
<script>var evil = 1;</script>
<p>Plain &amp; simple <b>text</b>.</p>
<ul><li>first</li><li>second</li></ul>
</body></html>"""


def test_drops_script_style_and_decodes_entities(project, cfg):
    out = html_to_text.run({"text": PAGE}, _ctx(project, cfg))
    assert "Title" in out
    assert "Plain & simple text." in out
    assert "- first" in out and "- second" in out
    assert "var evil" not in out       # <script> body gone
    assert "color:red" not in out      # <style> body gone
    assert "<" not in out              # no tags survive


def test_reads_from_project_file(project, cfg):
    (project.workspace_dir / "page.html").write_text("<p>hello <i>world</i></p>")
    out = html_to_text.run({"src": "workspace/page.html"}, _ctx(project, cfg))
    assert out == "hello world"


def test_dest_writes_to_workspace(project, cfg):
    out = html_to_text.run(
        {"text": "<h1>Doc</h1><p>body</p>", "dest": "extracted.txt"},
        _ctx(project, cfg),
    )
    assert out.startswith("wrote")
    assert (project.workspace_dir / "extracted.txt").read_text().startswith("Doc")


def test_requires_a_source(project, cfg):
    assert html_to_text.run({}, _ctx(project, cfg)).startswith("ERROR: provide")


def test_rejects_two_sources(project, cfg):
    out = html_to_text.run({"text": "<p>x</p>", "src": "a.html"}, _ctx(project, cfg))
    assert out.startswith("ERROR: give only one")


def test_has_no_url_parameter(project, cfg):
    # The whole safety argument: this tool never fetches, so it can't be a
    # second network ingress. Guard it in the schema, not just by convention.
    assert "url" not in html_to_text.TOOL["parameters"]["properties"]


def test_src_path_escape_denied(project, cfg):
    out = html_to_text.run({"src": "../../etc/hosts"}, _ctx(project, cfg))
    assert out.startswith("DENIED")


def test_dest_path_escape_denied(project, cfg):
    out = html_to_text.run(
        {"text": "<p>x</p>", "dest": "../escape.txt"}, _ctx(project, cfg)
    )
    assert out.startswith("DENIED")


def test_collapses_whitespace_and_blank_runs(project, cfg):
    messy = "<p>a</p>\n\n\n<div>   b    c   </div>"
    out = html_to_text.run({"text": messy}, _ctx(project, cfg))
    assert out == "a\n\nb c"


def test_empty_html_reports_no_text(project, cfg):
    out = html_to_text.run({"text": "<script>only()</script>"}, _ctx(project, cfg))
    assert out == "no readable text found in the HTML."
