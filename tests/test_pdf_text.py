"""pdf_text: extract text from a workspace PDF, local only.

The PDF fixtures are built in-process with correct xref offsets, so the tests
need no binary fixture file and no PDF-authoring dependency.
"""

import pytest

from hermes.toolbox import pdf_text
from hermes.tools.base import ToolContext


def _pypdf_ok():
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:
        return False


needs_pypdf = pytest.mark.skipif(
    not _pypdf_ok(), reason="pypdf not importable in this environment"
)


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


def _make_pdf(pages: list[str]) -> bytes:
    """A minimal valid multi-page PDF with one Tj text line per page."""
    kids_ids = [3 + i for i in range(len(pages))]  # page objects start at obj 3
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % k for k in kids_ids), len(pages)),
    ]
    content_start = 3 + len(pages)
    font_id = content_start + len(pages)
    for i, _ in enumerate(pages):
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
            % (content_start + i, font_id)
        )
    for text in pages:
        stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return bytes(out)


def _write_pdf(project, name, pages):
    p = project.workspace_dir / name
    p.write_bytes(_make_pdf(pages))
    return f"workspace/{name}"


@needs_pypdf
def test_extracts_text(project, cfg):
    src = _write_pdf(project, "doc.pdf", ["Hermes reads PDFs now"])
    out = pdf_text.run({"src": src}, _ctx(project, cfg))
    assert "Hermes reads PDFs now" in out
    assert "1 of 1 page(s)" in out


@needs_pypdf
def test_page_range(project, cfg):
    src = _write_pdf(project, "multi.pdf", ["AlphaPage", "BetaPage", "GammaPage"])
    out = pdf_text.run({"src": src, "pages": "2-3"}, _ctx(project, cfg))
    assert "BetaPage" in out and "GammaPage" in out
    assert "AlphaPage" not in out
    assert "2 of 3 page(s)" in out


@needs_pypdf
def test_single_page(project, cfg):
    src = _write_pdf(project, "multi.pdf", ["AlphaPage", "BetaPage"])
    out = pdf_text.run({"src": src, "pages": "1"}, _ctx(project, cfg))
    assert "AlphaPage" in out and "BetaPage" not in out


@needs_pypdf
def test_page_out_of_range(project, cfg):
    src = _write_pdf(project, "one.pdf", ["only page"])
    out = pdf_text.run({"src": src, "pages": "5"}, _ctx(project, cfg))
    assert out.startswith("ERROR: page 5 out of range")


@needs_pypdf
def test_bad_pages_spec(project, cfg):
    src = _write_pdf(project, "one.pdf", ["x"])
    out = pdf_text.run({"src": src, "pages": "abc"}, _ctx(project, cfg))
    assert out.startswith("ERROR: bad pages")


@needs_pypdf
def test_dest_writes_to_workspace(project, cfg):
    src = _write_pdf(project, "doc.pdf", ["saved content"])
    out = pdf_text.run({"src": src, "dest": "out.txt"}, _ctx(project, cfg))
    assert out.startswith("wrote")
    assert "saved content" in (project.workspace_dir / "out.txt").read_text()


def test_missing_file(project, cfg):
    out = pdf_text.run({"src": "workspace/nope.pdf"}, _ctx(project, cfg))
    # Either pypdf is absent (clean install ERROR) or the file is missing —
    # both are graceful ERROR strings, never a crash.
    assert out.startswith("ERROR")


def test_src_path_escape_denied(project, cfg):
    out = pdf_text.run({"src": "../../etc/passwd"}, _ctx(project, cfg))
    # DENIED (escape) unless pypdf is missing, in which case the import ERROR
    # fires first; both are safe, neither crashes.
    assert out.startswith("DENIED") or out.startswith("ERROR: pdf_text needs")


def test_graceful_when_pypdf_absent(project, cfg, monkeypatch):
    # Force the import to fail and confirm the operator gets a clear install hint.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no module named pypdf")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    (project.workspace_dir / "d.pdf").write_bytes(b"%PDF-1.4")
    out = pdf_text.run({"src": "workspace/d.pdf"}, _ctx(project, cfg))
    assert out.startswith("ERROR: pdf_text needs the 'pypdf' package")


def test_no_url_parameter(project, cfg):
    assert "url" not in pdf_text.TOOL["parameters"]["properties"]
