import json

from hermes.twin import clone as clone_mod
from hermes.twin.clone import clone, expand


class FakeFetch:
    def __init__(self, responses, default=(404, {"content-type": "text/plain"}, "nope")):
        self.responses = responses
        self.default = default
        self.calls = []

    def __call__(self, method, url, headers=None, body=None, timeout=45):
        self.calls.append((method, url))
        for suffix, resp in self.responses.items():
            if url.endswith(suffix):
                return resp
        return self.default


def test_clone_records_and_seals(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    fetch = FakeFetch({
        "/": (200, {"content-type": "text/html"}, "<a href='/about'>about</a>"),
        "/about": (200, {"content-type": "text/html"}, "about page"),
    })
    report = clone(twin, "https://api.example.com", fetch=fetch, delay=0,
                   include_discovery=False)
    assert twin.is_sealed()
    assert report["recorded"] >= 2
    assert twin.respond("GET", "/about").response_body == "about page"


def test_clone_follows_same_origin_links_only(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    fetch = FakeFetch({
        "/": (200, {"content-type": "text/html"},
              "<a href='/in'>in</a><a href='https://evil.com/out'>out</a>"),
        "/in": (200, {"content-type": "text/html"}, "inside"),
    })
    clone(twin, "https://api.example.com", fetch=fetch, delay=0, include_discovery=False)
    urls = {u for _, u in fetch.calls}
    assert any(u.endswith("/in") for u in urls)
    assert not any("evil.com" in u for u in urls)  # off-origin never followed


def test_clone_skips_static_assets(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://shop.example.com")
    home = ("<a href='/products'>products</a>"
            "<a href='/logo.png'>logo</a>"
            "<link href='/style.css'>"
            "<a href='/bundle.js'>js</a>")
    fetch = FakeFetch({
        "/": (200, {"content-type": "text/html"}, home),
        "/products": (200, {"content-type": "text/html"}, "products page"),
    })
    clone(twin, "https://shop.example.com", fetch=fetch, delay=0,
          include_discovery=False)
    urls = {u for _, u in fetch.calls}
    assert any(u.endswith("/products") for u in urls)   # pages still followed
    assert not any(u.endswith("/logo.png") for u in urls)
    assert not any(u.endswith("/style.css") for u in urls)
    assert not any(u.endswith("/bundle.js") for u in urls)


def test_clone_mines_openapi_spec(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    spec = {"paths": {"/users": {"get": {}}, "/health": {"get": {}}}}
    fetch = FakeFetch({
        "/openapi.json": (200, {"content-type": "application/json"}, json.dumps(spec)),
        "/users": (200, {"content-type": "application/json"}, '[{"id":1}]'),
        "/health": (200, {"content-type": "application/json"}, '{"ok":true}'),
        "/": (200, {"content-type": "application/json"}, "{}"),
    })
    clone(twin, "https://api.example.com", fetch=fetch, delay=0)
    assert twin.read_manifest()["has_spec"] is True
    assert twin.respond("GET", "/users").response_body == '[{"id":1}]'


def test_clone_fills_spec_path_params_from_examples(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    spec = {"paths": {"/users/{id}": {"get": {
        "parameters": [{"name": "id", "in": "path", "example": 7}]}}}}
    fetch = FakeFetch({
        "/openapi.json": (200, {"content-type": "application/json"}, json.dumps(spec)),
        "/users/7": (200, {"content-type": "application/json"}, '{"id":7}'),
        "/": (200, {"content-type": "application/json"}, "{}"),
    })
    clone(twin, "https://api.example.com", fetch=fetch, delay=0)
    assert twin.respond("GET", "/users/7").response_body == '{"id":7}'


def test_clone_honors_max(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    links = "".join(f"<a href='/p{i}'>{i}</a>" for i in range(20))
    fetch = FakeFetch({"/": (200, {"content-type": "text/html"}, links)},
                      default=(200, {"content-type": "text/html"}, "x"))
    report = clone(twin, "https://api.example.com", fetch=fetch, delay=0,
                   include_discovery=False, max_exchanges=5)
    assert report["recorded"] == 5


def test_clone_fingerprints_stack(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://blog.example.com")
    wp_home = ('<meta name="generator" content="WordPress 6.4"/>'
               '<link href="/wp-content/themes/t/style.css">')
    fetch = FakeFetch(
        {"/": (200, {"content-type": "text/html", "server": "Apache/2.4",
                     "x-powered-by": "PHP/8.1"}, wp_home)},
        default=(404, {"content-type": "text/html"}, "nope"))
    report = clone(twin, "https://blog.example.com", fetch=fetch, delay=0,
                   include_discovery=False)
    assert report["stack"]["product"] == "WordPress"
    assert report["stack"]["kind"] == "known_stack"
    # persisted on the model, surfaced in the summary
    assert twin.stack["product_version"] == "6.4"
    assert "WordPress 6.4" in twin.summary()


def test_expand_grows_sealed_model(project, monkeypatch):
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    twin.seal()
    fetch = FakeFetch({"/users/2": (200, {"content-type": "application/json"}, '{"id":2}')})
    report = expand(twin, "https://api.example.com", ["/users/2"], fetch=fetch, delay=0)
    assert report["added"] == 1
    assert twin.is_sealed()  # re-sealed after growth
    assert twin.respond("GET", "/users/2").response_body == '{"id":2}'


def test_expand_keeps_fingerprintable_headers(project, monkeypatch):
    # expand() must preserve the response headers clone()/reground() keep, so the
    # stack fingerprint still sees Server/X-Powered-By on grown exchanges.
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    twin = project.twin()
    twin.init(source="https://api.example.com")
    twin.seal()
    fetch = FakeFetch({"/wp-json": (200, {"content-type": "application/json",
                                          "server": "Apache/2.4.52",
                                          "x-powered-by": "PHP/8.1"}, "{}")})
    expand(twin, "https://api.example.com", ["/wp-json"], fetch=fetch, delay=0)
    headers = twin.respond("GET", "/wp-json").response_headers
    assert headers.get("server") == "Apache/2.4.52"
    assert headers.get("x-powered-by") == "PHP/8.1"
