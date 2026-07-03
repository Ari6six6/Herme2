"""extract_code: pull code blocks out of HTML/markdown the readable-text
renderer would drop or flatten."""

from hermes.toolbox import extract_code
from hermes.tools.base import ToolContext


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


def test_extracts_script_tag_that_http_request_drops(project, cfg):
    html = """
    <html><body>
      <p>Here is the solution:</p>
      <script type="text/javascript">
const greet = (name) =&gt; `hi ${name}`;
console.log(greet("world"));
      </script>
    </body></html>
    """
    out = extract_code.run({"text": html}, _ctx(project, cfg))
    assert "const greet = (name) =>" in out  # entity decoded
    assert 'console.log(greet("world"));' in out
    assert "Here is the solution" not in out  # prose excluded


def test_pre_code_nesting_yields_single_block(project, cfg):
    html = "<pre><code class='language-python'>print('x')\nprint('y')</code></pre>"
    out = extract_code.run({"text": html}, _ctx(project, cfg))
    assert out.count("--- block") == 1
    assert "[python]" in out
    assert "print('x')\nprint('y')" in out


def test_markdown_fences(project, cfg):
    md = "intro\n\n```js\nlet a = 1;\n```\n\nmore\n\n~~~\nplain block\n~~~\n"
    out = extract_code.run({"text": md}, _ctx(project, cfg))
    assert "let a = 1;" in out
    assert "plain block" in out
    assert "2 code block" in out


def test_lang_filter(project, cfg):
    md = "```js\nA\n```\n```python\nB\n```\n"
    out = extract_code.run({"text": md, "lang": "python"}, _ctx(project, cfg))
    assert "B" in out
    assert "\nA\n" not in out


def test_save_writes_block_to_workspace(project, cfg):
    md = "```js\nconsole.log(1)\n```\n```js\nconsole.log(2)\n```\n"
    out = extract_code.run({"text": md, "save": "snippet.js", "index": 2}, _ctx(project, cfg))
    assert "wrote block 2" in out
    written = (project.workspace_dir / "snippet.js").read_text()
    assert written == "console.log(2)\n"


def test_save_rejects_escape(project, cfg):
    md = "```\nx\n```\n"
    out = extract_code.run({"text": md, "save": "../escape.js"}, _ctx(project, cfg))
    assert out.startswith("DENIED")


def test_reads_project_file(project, cfg):
    (project.workspace_dir / "page.html").write_text("<pre>data = 42</pre>")
    out = extract_code.run({"path": "workspace/page.html"}, _ctx(project, cfg))
    assert "data = 42" in out


def test_requires_a_source(project, cfg):
    assert extract_code.run({}, _ctx(project, cfg)).startswith("ERROR: provide one of")


def test_rejects_multiple_sources(project, cfg):
    out = extract_code.run({"text": "x", "url": "http://e"}, _ctx(project, cfg))
    assert out.startswith("ERROR: give exactly one")


def test_no_blocks_message(project, cfg):
    out = extract_code.run({"text": "just prose, nothing fenced"}, _ctx(project, cfg))
    assert "no code blocks" in out
