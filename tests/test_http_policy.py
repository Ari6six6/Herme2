from hermes import http_policy


def test_empty_by_default(cfg):
    assert not http_policy.is_allowed(cfg, "api.example.com", "GET")


def test_exact_domain_default_methods(cfg):
    cfg.set("http_allow", [{"domain": "api.example.com"}])
    assert http_policy.is_allowed(cfg, "api.example.com", "GET")
    assert http_policy.is_allowed(cfg, "api.example.com", "HEAD")
    assert not http_policy.is_allowed(cfg, "api.example.com", "POST")
    assert not http_policy.is_allowed(cfg, "other.example.com", "GET")


def test_explicit_methods(cfg):
    cfg.set("http_allow", [{"domain": "api.example.com", "methods": ["POST"]}])
    assert http_policy.is_allowed(cfg, "api.example.com", "POST")
    assert http_policy.is_allowed(cfg, "api.example.com", "post")
    assert not http_policy.is_allowed(cfg, "api.example.com", "GET")


def test_wildcard_methods(cfg):
    cfg.set("http_allow", [{"domain": "api.example.com", "methods": ["*"]}])
    assert http_policy.is_allowed(cfg, "api.example.com", "DELETE")


def test_wildcard_subdomain(cfg):
    cfg.set("http_allow", [{"domain": "*.example.com", "methods": ["GET"]}])
    assert http_policy.is_allowed(cfg, "api.example.com", "GET")
    assert http_policy.is_allowed(cfg, "example.com", "GET")
    assert not http_policy.is_allowed(cfg, "evil-example.com", "GET")
    assert not http_policy.is_allowed(cfg, "notexample.com", "GET")


def test_no_domain_never_matches(cfg):
    cfg.set("http_allow", [{"domain": "api.example.com"}])
    assert not http_policy.is_allowed(cfg, None, "GET")
    assert not http_policy.is_allowed(cfg, "", "GET")
