"""base64_codec: encode/decode, binary-safe, tolerant decode."""

import base64

from hermes.toolbox import base64_codec
from hermes.tools.base import ToolContext


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


def test_encode_text(project, cfg):
    out = base64_codec.run({"action": "encode", "text": "hello"}, _ctx(project, cfg))
    assert out == base64.b64encode(b"hello").decode()


def test_decode_text(project, cfg):
    payload = base64.b64encode(b"console.log('hi')").decode()
    out = base64_codec.run({"action": "decode", "text": payload}, _ctx(project, cfg))
    assert out == "console.log('hi')"


def test_decode_tolerates_whitespace_and_missing_padding(project, cfg):
    raw = base64.b64encode(b"some longer payload here").decode().rstrip("=")
    chunked = raw[:8] + "\n" + raw[8:]  # newline + stripped padding
    out = base64_codec.run({"action": "decode", "text": chunked}, _ctx(project, cfg))
    assert out == "some longer payload here"


def test_decode_urlsafe_alphabet(project, cfg):
    data = b"\xff\xff\xfe?subjects"
    encoded = base64.urlsafe_b64encode(data).decode()
    out = base64_codec.run(
        {"action": "decode", "text": encoded, "dest": "out.bin"}, _ctx(project, cfg)
    )
    assert "wrote" in out
    assert (project.workspace_dir / "out.bin").read_bytes() == data


def test_decode_binary_without_dest_refuses(project, cfg):
    encoded = base64.b64encode(b"\x00\x01\x02\xff").decode()
    out = base64_codec.run({"action": "decode", "text": encoded}, _ctx(project, cfg))
    assert "binary" in out and "dest" in out


def test_decode_invalid(project, cfg):
    out = base64_codec.run({"action": "decode", "text": "!!!not base64!!!"}, _ctx(project, cfg))
    assert out.startswith("ERROR: not valid base64")


def test_encode_from_file_to_dest(project, cfg):
    (project.workspace_dir / "in.bin").write_bytes(bytes(range(256)))
    out = base64_codec.run(
        {"action": "encode", "src": "workspace/in.bin", "dest": "in.b64"},
        _ctx(project, cfg),
    )
    assert "wrote" in out
    written = (project.workspace_dir / "in.b64").read_bytes()
    assert base64.b64decode(written) == bytes(range(256))


def test_requires_input(project, cfg):
    out = base64_codec.run({"action": "encode"}, _ctx(project, cfg))
    assert out.startswith("ERROR: provide")


def test_bad_action(project, cfg):
    out = base64_codec.run({"action": "frobnicate", "text": "x"}, _ctx(project, cfg))
    assert out.startswith("ERROR: action must be")


def test_dest_escape_denied(project, cfg):
    out = base64_codec.run(
        {"action": "encode", "text": "x", "dest": "../escape"}, _ctx(project, cfg)
    )
    assert out.startswith("DENIED")
