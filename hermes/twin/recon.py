"""Recon: fingerprint a target's stack so the builder knows what to reconstruct.

Two kinds of target, two kinds of twin:

  - **opaque / bespoke** — hidden logic we can only mirror by its observable
    behavior (the behavioral twin: recorded responses).
  - **known open-source stack** (WordPress, Drupal, Django, Rails, ...) — we can
    identify it from the outside and reconstruct the REAL software in the sandbox,
    because that software is public and downloadable. Far higher fidelity than a
    mock: the twin literally *is* the software.

This module is deterministic detection over responses the recon pass already
fetched — server, runtime, framework/CMS and versions, read from headers,
generator tags, cookies, and well-known paths. It tells the builder which stack to
stand up.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# (label, header-name, regex over the value, optional version group)
_HEADER_RULES = [
    ("server", "server", re.compile(r"^([A-Za-z\-]+)(?:/([\d.]+))?"), True),
    ("runtime", "x-powered-by", re.compile(r"(PHP|ASP\.NET|Express|Servlet)[/ ]?([\d.]*)", re.I), True),
    ("runtime", "x-aspnet-version", re.compile(r"([\d.]+)"), False),
]

# Recognizable applications/frameworks we could actually stand up. Each: markers
# in headers (cookie/header substrings) and body, plus a version extractor.
@dataclass
class _Product:
    name: str
    cookie_markers: tuple = ()
    body_markers: tuple = ()
    path_markers: tuple = ()
    version_re: "re.Pattern | None" = None


_PRODUCTS = [
    _Product("WordPress",
             cookie_markers=("wordpress_", "wp-settings"),
             body_markers=("/wp-content/", "/wp-includes/", "wp-json"),
             path_markers=("/wp-login.php", "/wp-json", "/wp-admin"),
             version_re=re.compile(r'name="generator" content="WordPress ([\d.]+)"', re.I)),
    _Product("Drupal",
             cookie_markers=("SESS",),
             body_markers=("Drupal.settings", "/sites/default/", "drupal.js"),
             path_markers=("/user/login", "/core/misc/drupal.js"),
             version_re=re.compile(r'name="generator" content="Drupal ([\d.]+)', re.I)),
    _Product("Joomla", body_markers=("/media/jui/", "com_content"),
             path_markers=("/administrator",),
             version_re=re.compile(r'name="generator" content="Joomla! ([\d.]+)', re.I)),
    _Product("Django", cookie_markers=("csrftoken", "django"),
             body_markers=("csrfmiddlewaretoken",), path_markers=("/admin/login/",)),
    _Product("Ruby on Rails", cookie_markers=("_rails", "_session_id"),
             body_markers=('name="csrf-param"',)),
    _Product("Laravel", cookie_markers=("laravel_session", "XSRF-TOKEN")),
    _Product("Express", cookie_markers=("connect.sid",)),
    _Product("Next.js", body_markers=("__NEXT_DATA__", "/_next/static/")),
]


@dataclass
class StackReport:
    kind: str = "opaque"           # "known_stack" | "opaque"
    product: str = ""              # e.g. "WordPress"
    product_version: str = ""
    server: str = ""               # e.g. "Apache/2.4.52"
    runtime: str = ""              # e.g. "PHP/8.1"
    confidence: str = "low"        # low | medium | high
    signals: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def reconstructable(self) -> bool:
        return self.kind == "known_stack"

    def summary(self) -> str:
        if self.kind == "known_stack":
            ver = f" {self.product_version}" if self.product_version else ""
            stack = ", ".join(x for x in (self.server, self.runtime) if x)
            return (f"known stack: {self.product}{ver}"
                    + (f" on {stack}" if stack else "")
                    + f"  ({self.confidence} confidence) — reconstruct the real software")
        hints = ", ".join(x for x in (self.server, self.runtime) if x)
        return ("opaque service" + (f" ({hints})" if hints else "")
                + " — mirror observable behavior")


def _header(headers: dict, name: str) -> str:
    for k, v in (headers or {}).items():
        if k.lower() == name:
            return str(v)
    return ""


def fingerprint(exchanges) -> StackReport:
    """Detect the stack from recorded exchanges (each with .response_headers,
    .response_body, .path). Deterministic, read-only over what we already have."""
    report = StackReport()
    bodies = []
    paths = set()
    all_headers: dict[str, str] = {}
    cookie_blob = ""
    for ex in exchanges:
        headers = getattr(ex, "response_headers", {}) or {}
        for k, v in headers.items():
            all_headers.setdefault(k.lower(), str(v))
            if k.lower() == "set-cookie":
                cookie_blob += " " + str(v)
        bodies.append(getattr(ex, "response_body", "") or "")
        paths.add(getattr(ex, "path", "") or "")
    body_blob = "\n".join(bodies)

    # server / runtime from headers
    server_val = _header(all_headers, "server")
    if server_val:
        report.server = server_val.split(";")[0].strip()
        report.signals.append(f"Server: {report.server}")
    powered = _header(all_headers, "x-powered-by")
    if powered:
        report.runtime = powered.split(",")[0].strip()
        report.signals.append(f"X-Powered-By: {report.runtime}")

    # application / framework
    best = None
    best_hits = 0
    for prod in _PRODUCTS:
        hits = []
        for m in prod.cookie_markers:
            if m.lower() in cookie_blob.lower():
                hits.append(f"cookie~{m}")
        for m in prod.body_markers:
            if m in body_blob:
                hits.append(f"body~{m}")
        for m in prod.path_markers:
            if any(p == m or p.startswith(m) for p in paths):
                hits.append(f"path~{m}")
        if len(hits) > best_hits:
            best, best_hits = prod, len(hits)
            best_hits_list = hits
    if best is not None:
        report.kind = "known_stack"
        report.product = best.name
        report.signals.extend(best_hits_list)
        if best.version_re:
            m = best.version_re.search(body_blob)
            if m:
                report.product_version = m.group(1)
                report.signals.append(f"version {report.product_version}")
        report.confidence = (
            "high" if best_hits >= 2 or report.product_version else "medium"
        )
    return report
