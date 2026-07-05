from hermes.twin.model import (
    Exchange,
    TwinModel,
    request_key,
    route_template,
)


def _ex(method="GET", path="/users/1", query="", status=200, body='{"id":1}',
        request_body=None):
    return Exchange(method=method, path=path, query=query, status=status,
                    response_body=body, request_body=request_body)


def test_request_key_order_insensitive_query():
    assert request_key("get", "/s", "b=2&a=1") == request_key("GET", "/s", "a=1&b=2")


def test_request_key_separates_method_and_body():
    assert request_key("GET", "/x") != request_key("POST", "/x")
    assert request_key("POST", "/x", body="a") != request_key("POST", "/x", body="b")


def test_route_template_collapses_ids():
    assert route_template("/users/42/posts/99") == "/users/{id}/posts/{id}"
    assert route_template("/users/550e8400-e29b-41d4-a716-446655440000") == "/users/{id}"
    assert route_template("/about") == "/about"


def test_init_open_then_seal(project):
    twin = project.twin()
    twin.init(source="https://api.example.com", mission="reimplement /users",
              win_condition="byte-match")
    assert twin.exists() and not twin.is_sealed()
    assert twin.source == "https://api.example.com"
    assert twin.mission == "reimplement /users"
    twin.add_exchange(_ex())
    twin.seal()
    assert twin.is_sealed()
    assert twin.read_manifest()["exchange_count"] == 1


def test_respond_exact_match_only(project):
    twin = project.twin()
    twin.init(source="x")
    twin.add_exchange(_ex(path="/users/1", body='{"id":1}'))
    assert twin.respond("GET", "/users/1").response_body == '{"id":1}'
    assert twin.respond("GET", "/users/2") is None  # never fabricates


def test_add_exchange_deduplicates(project):
    twin = project.twin()
    twin.init(source="x")
    twin.add_exchange(_ex(path="/a", body="1"))
    twin.add_exchange(_ex(path="/a", body="1"))
    assert len(twin.exchanges()) == 1


def test_sealed_model_refuses_growth_until_unsealed(project):
    twin = project.twin()
    twin.init(source="x")
    twin.add_exchange(_ex())
    twin.seal()
    try:
        twin.add_exchange(_ex(path="/other"))
        assert False, "sealed twin must refuse new exchanges"
    except ValueError:
        pass
    twin.unseal()
    twin.add_exchange(_ex(path="/other"))
    twin.seal()
    assert len(twin.exchanges()) == 2


def test_route_map_groups_examples(project):
    twin = project.twin()
    twin.init(source="x")
    twin.add_exchange(_ex(path="/users/1"))
    twin.add_exchange(_ex(path="/users/2"))
    twin.add_exchange(_ex(path="/about", body="hi"))
    rmap = dict(((m, t), n) for m, t, n in twin.route_map())
    assert rmap[("GET", "/users/{id}")] == 2
    assert rmap[("GET", "/about")] == 1


def test_summary_reports_state_and_goal(project):
    twin = project.twin()
    twin.init(source="https://api.example.com", win_condition="parity on /users")
    twin.add_exchange(_ex())
    assert "OPEN" in twin.summary()
    twin.seal()
    out = twin.summary()
    assert "sealed" in out and "parity on /users" in out
