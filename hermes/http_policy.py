"""Operator-configured allowlist for outbound HTTP requests.

Two gates already govern http_request: the tool itself always confirms
state-changing methods (hermes/tools/web.py), and a tainted turn (feature 8)
requires approval for everything regardless of method — both re-prompt every
run since neither persists past the in-memory ToolContext. This is a third,
explicit list the operator edits via `allow` (or `config set http_allow ...`):
domain/method pairs that never prompt, in any turn, tainted or not. Empty by
default — nothing is auto-approved until the operator adds it themselves.
"""

from __future__ import annotations


def _domain_matches(domain: str, rule_domain: str) -> bool:
    rule_domain = rule_domain.lower()
    if rule_domain.startswith("*."):
        suffix = rule_domain[1:]  # ".example.com" — matches sub.example.com
        return domain == rule_domain[2:] or domain.endswith(suffix)
    return domain == rule_domain


def is_allowed(cfg, domain: str | None, method: str) -> bool:
    """True if (domain, method) matches an operator-configured http_allow rule."""
    if not domain:
        return False
    method = method.upper()
    for rule in cfg.get("http_allow") or []:
        rule_domain = rule.get("domain", "")
        if not rule_domain or not _domain_matches(domain, rule_domain):
            continue
        methods = rule.get("methods") or ["GET", "HEAD"]
        if "*" in methods or method in (m.upper() for m in methods):
            return True
    return False
