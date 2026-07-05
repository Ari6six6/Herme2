"""Prove the twin is a real, accurate runtime — start it and hit it over HTTP."""

import threading
from contextlib import contextmanager

import httpx

from hermes.twin import server as twin_server
from hermes.twin.model import Exchange, TwinModel


@contextmanager
def running_twin(model_dir):
    srv = twin_server.make_server(str(model_dir), port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        t.join(timeout=2)


def _seed(project, exchanges):
    twin = project.twin()
    twin.init(source="https://api.example.com")
    for ex in exchanges:
        twin.add_exchange(ex)
    twin.seal()
    return twin


def test_twin_serves_exact_recorded_response(project):
    _seed(project, [Exchange(method="GET", path="/users/1", status=200,
                             response_body='{"id":1,"name":"ada"}',
                             content_type="application/json")])
    with running_twin(project.twin_dir) as base:
        r = httpx.get(base + "/users/1")
    assert r.status_code == 200
    assert r.json() == {"id": 1, "name": "ada"}
    assert r.headers["x-twin"] == "exact"  # served real, not synthesized


def test_twin_preserves_status_and_body_for_errors(project):
    _seed(project, [Exchange(method="GET", path="/missing", status=404,
                             response_body='{"error":"not found"}',
                             content_type="application/json")])
    with running_twin(project.twin_dir) as base:
        r = httpx.get(base + "/missing")
    assert r.status_code == 404  # a real captured 404, replayed faithfully
    assert r.headers["x-twin"] == "exact"
    assert r.json()["error"] == "not found"


def test_twin_misses_instead_of_fabricating(project):
    _seed(project, [Exchange(method="GET", path="/users/1", status=200,
                             response_body="x", content_type="text/plain")])
    with running_twin(project.twin_dir) as base:
        r = httpx.get(base + "/users/999")
    assert r.status_code == 504  # miss, not an invented 200
    assert r.headers["x-twin"] == "miss"
    assert r.json()["twin_miss"]["path"] == "/users/999"


def test_twin_matches_query_order_insensitively(project):
    _seed(project, [Exchange(method="GET", path="/search", query="a=1&b=2",
                             status=200, response_body="hit", content_type="text/plain")])
    with running_twin(project.twin_dir) as base:
        r = httpx.get(base + "/search?b=2&a=1")
    assert r.status_code == 200
    assert r.text == "hit"


def test_twin_distinguishes_methods(project):
    _seed(project, [
        Exchange(method="GET", path="/x", status=200, response_body="g", content_type="text/plain"),
        Exchange(method="POST", path="/x", status=201, response_body="p", content_type="text/plain"),
    ])
    with running_twin(project.twin_dir) as base:
        assert httpx.get(base + "/x").text == "g"
        assert httpx.post(base + "/x").status_code == 201


def test_server_key_matches_model_key():
    # The standalone server must canonicalize requests identically to the model,
    # or exact matches would silently miss.
    from hermes.twin import model
    for args in [("GET", "/a"), ("post", "/a", "b=2&a=1", "body"),
                 ("GET", "/s?z=1&a=2")]:
        assert twin_server.request_key(*args) == model.request_key(*args)
