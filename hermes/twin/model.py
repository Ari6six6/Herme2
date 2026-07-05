"""The target model: everything the clone engine learned about a service.

This is not a passive recording — it is the seed for a *runtime twin*, a local
service that behaves like the target so the agent builds against a faithful, safe
copy instead of the live system. The model holds real exchanges (a request and
the service's actual response), the route map inferred from them, and any API
spec the clone found.

Accuracy rule: the twin serves a real captured response exactly, or it declares a
miss. It never invents a response — so everything the agent builds against is
something the real service really did.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

# Path segments that are almost certainly identifiers, not route structure —
# used to infer templates like /users/{id} from observed paths.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEXISH_RE = re.compile(r"^[0-9a-f]{12,}$", re.I)


def request_key(method: str, path: str, query: str = "", body: str | None = None) -> str:
    """Canonical identity for a request: stable across header noise and query
    ordering. An explicit `query` overrides any embedded in `path`."""
    method = (method or "GET").upper()
    split = urlsplit(path)
    p = split.path or "/"
    raw_query = query or split.query or ""
    qn = urlencode(sorted(parse_qsl(raw_query, keep_blank_values=True)))
    b = (body or "").strip()
    return f"{method} {p}?{qn}\n{b}"


def _is_param_segment(seg: str) -> bool:
    return bool(seg) and (seg.isdigit() or bool(_UUID_RE.match(seg)) or bool(_HEXISH_RE.match(seg)))


def route_template(path: str) -> str:
    """Collapse identifier-looking segments to {id} so /users/42 and /users/99
    share one route in the map."""
    parts = (urlsplit(path).path or path).split("/")
    return "/".join("{id}" if _is_param_segment(s) else s for s in parts) or "/"


@dataclass
class Exchange:
    """One real request -> response pair observed from the target."""
    method: str
    path: str
    status: int
    response_body: str
    query: str = ""
    request_body: str | None = None
    content_type: str = ""
    response_headers: dict = field(default_factory=dict)
    source: str = "crawl"  # crawl | spec | seed | expand
    captured_at: str = ""

    def key(self) -> str:
        return request_key(self.method, self.path, self.query, self.request_body)

    def label(self) -> str:
        return f"{self.method} {self.path}{('?' + self.query) if self.query else ''}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Exchange":
        known = set(Exchange.__dataclass_fields__)  # type: ignore[attr-defined]
        return Exchange(**{k: v for k, v in d.items() if k in known})


class TwinModel:
    """A directory holding the model of one target (inside a project's `twin/`).

      manifest.json    source, mission, win condition, sealed flag, counts
      exchanges.jsonl  one real request/response pair per line
      spec.json        captured API spec (OpenAPI/Swagger), when found
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    def for_project(cls, project) -> "TwinModel":
        """The twin model living under a project's `twin/` directory."""
        return cls(project.twin_dir)

    # -- layout ------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def exchanges_path(self) -> Path:
        return self.root / "exchanges.jsonl"

    @property
    def spec_path(self) -> Path:
        return self.root / "spec.json"

    def exists(self) -> bool:
        return self.manifest_path.exists()

    # -- manifest ----------------------------------------------------------
    def read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def write_manifest(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(data, indent=2) + "\n")

    def _patch_manifest(self, **fields) -> None:
        m = self.read_manifest()
        m.update(fields)
        self.write_manifest(m)

    def is_sealed(self) -> bool:
        return bool(self.read_manifest().get("sealed"))

    @property
    def source(self) -> str:
        return self.read_manifest().get("source", "")

    @property
    def mode(self) -> str:
        return self.read_manifest().get("mode", "url")

    @property
    def mission(self) -> str:
        return self.read_manifest().get("mission", "")

    @property
    def win_condition(self) -> str:
        return self.read_manifest().get("win_condition", "")

    # -- lifecycle ---------------------------------------------------------
    def init(self, source: str, mode: str = "url", mission: str = "",
             win_condition: str = "") -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.exchanges_path.write_text("")
        self.write_manifest({
            "source": source,
            "mode": mode,
            "mission": mission.strip(),
            "win_condition": win_condition.strip(),
            "sealed": False,
            "exchange_count": 0,
            "has_spec": False,
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "sealed_at": "",
        })

    def set_win_condition(self, text: str) -> None:
        self._patch_manifest(win_condition=text.strip())

    def set_mission(self, text: str) -> None:
        self._patch_manifest(mission=text.strip())

    def store_spec(self, spec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.spec_path.write_text(json.dumps(spec, indent=2))
        self._patch_manifest(has_spec=True)

    def store_stack(self, stack: dict) -> None:
        """Record the recon fingerprint (which kind of twin to stand up)."""
        self._patch_manifest(stack=stack)

    @property
    def stack(self) -> dict:
        return self.read_manifest().get("stack", {})

    def add_exchange(self, ex: Exchange) -> None:
        """Append a real exchange. Refused once sealed — a sealed twin is frozen
        so the agent can trust it never shifts under it. (Use unseal() to grow.)"""
        if self.is_sealed():
            raise ValueError("twin is sealed — open it with unseal() to add more")
        self.root.mkdir(parents=True, exist_ok=True)
        if not ex.captured_at:
            ex.captured_at = time.strftime("%Y-%m-%d %H:%M:%S")
        if self.respond(ex.method, ex.path, ex.query, ex.request_body) is not None:
            return  # already known — don't duplicate
        with self.exchanges_path.open("a") as f:
            f.write(json.dumps(ex.to_dict(), ensure_ascii=False) + "\n")

    def upsert_exchange(self, ex: Exchange) -> None:
        """Replace the stored exchange with the same request key (or add it).
        Used when re-grounding corrects a sample that drifted from the target."""
        if self.is_sealed():
            raise ValueError("twin is sealed — open it with unseal() to correct it")
        if not ex.captured_at:
            ex.captured_at = time.strftime("%Y-%m-%d %H:%M:%S")
        kept = [e for e in self.exchanges() if e.key() != ex.key()]
        kept.append(ex)
        self.exchanges_path.write_text(
            "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in kept) + "\n"
        )

    def exchanges(self) -> list[Exchange]:
        if not self.exchanges_path.exists():
            return []
        out = []
        for line in self.exchanges_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Exchange.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def seal(self) -> None:
        self._patch_manifest(
            sealed=True,
            sealed_at=time.strftime("%Y-%m-%d %H:%M"),
            exchange_count=len(self.exchanges()),
        )

    def unseal(self) -> None:
        """Reopen for growth — used only by the clone layer when expanding the
        model to cover a miss. Re-seal when done."""
        self._patch_manifest(sealed=False)

    # -- reconstruction recipe ---------------------------------------------
    @property
    def recipe_path(self) -> Path:
        return self.root / "recipe.jsonl"

    def add_step(self, cmd: str, note: str = "") -> None:
        """Capture one working reconstruction step. The recipe is the cheap way
        to rebuild the stack later — the expensive part (deriving the steps) is
        paid once and replayed."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.recipe_path.open("a") as f:
            f.write(json.dumps({"cmd": cmd, "note": note,
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                               ensure_ascii=False) + "\n")

    def recipe(self) -> list[dict]:
        if not self.recipe_path.exists():
            return []
        out = []
        for line in self.recipe_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # -- the ground-truth lookup -------------------------------------------
    def respond(self, method: str, path: str, query: str = "",
                body: str | None = None) -> Exchange | None:
        """The real captured response for this exact request, or None. A pure
        lookup in the frozen model — it never reaches the network."""
        wanted = request_key(method, path, query, body)
        for ex in self.exchanges():
            if ex.key() == wanted:
                return ex
        return None

    def route_map(self) -> list[tuple[str, str, int]]:
        """(method, route-template, example-count) for the observed surface."""
        seen: dict[tuple[str, str], int] = {}
        for ex in self.exchanges():
            k = (ex.method, route_template(ex.path))
            seen[k] = seen.get(k, 0) + 1
        return sorted((m, t, n) for (m, t), n in seen.items())

    def summary(self) -> str:
        m = self.read_manifest()
        if not m:
            return "(no target set)"
        state = "sealed (frozen twin)" if m.get("sealed") else "OPEN (cloning not finished)"
        lines = [
            f"target:  {m.get('source', '?')}  [{m.get('mode', 'url')}]",
            f"state:   {state}  ·  {m.get('exchange_count', len(self.exchanges()))} exchange(s)"
            f"{'  ·  API spec captured' if m.get('has_spec') else ''}",
        ]
        stack = m.get("stack") or {}
        if stack:
            from hermes.twin.recon import StackReport
            lines.append("stack:   " + StackReport(**stack).summary())
        lines += [
            f"mission: {m.get('mission') or '(none set)'}",
            f"win:     {m.get('win_condition') or '(none set)'}",
        ]
        return "\n".join(lines)
