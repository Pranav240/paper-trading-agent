"""
Tests for the public demo (docs/ static site, docs/data snapshot, public_api/).

The demo is published to the open internet, so the tests are about what must
never be there (secrets, personal data, write routes, a model client) and
about the site actually being whole (schemas, links). No database, no network.

The link and redaction tests run on the staged Pages artifact built by
scripts/build_site.py, i.e. exactly what is deployed, as well as on the
source folders.
"""

import ast
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DOCS = ROOT / "docs"
DATA = DOCS / "data"
API = ROOT / "public_api"
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".md", ".py", ".txt", ".yml", ".yaml", ".toml", ".svg", ""}

# Same families as the export's redaction pass (scripts/export_public_snapshot.py),
# kept separate on purpose: the test must not share a bug with the thing it checks.
SECRET_PATTERNS = {
    "api key": re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9\-]{10,})"),
    "database url": re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb|redis)://", re.I),
    "aws arn": re.compile(r"arn:aws[a-z\-]*:"),
    "email address": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "aws account id": re.compile(r"(?:(?:aws|account|iam)[^\n]{0,40}(?<!\d)\d{12}(?!\d)|(?<!\d)\d{12}(?!\d)[^\n]{0,40}(?:aws|account|iam))", re.I),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def text_files(*roots: Path):
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and "__pycache__" not in path.parts:
                yield path


@pytest.fixture(scope="module")
def staged(tmp_path_factory) -> Path:
    from build_site import build

    return build(tmp_path_factory.mktemp("site") / "_site")


class Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in {"href", "src"} and v:
                self.refs.append(v)


# ---------------------------------------------------------------- redaction --

def test_nothing_secret_like_in_docs_or_public_api():
    hits = []
    for path in text_files(DOCS, API):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {name}")  # the value is never printed
    assert not hits, "secret-like strings found:\n" + "\n".join(hits)


def test_redaction_patterns_actually_catch_things():
    """Guard the guard: a test that cannot fail proves nothing."""
    samples = {
        "api key": "sk-" + "a" * 24,
        "database url": "postgresql://u:p@h/db",
        "aws arn": "arn:aws:iam::123456789012:role/x",
        "email address": "someone@example.com",
        "aws account id": "AWS account 123456789012",
    }
    for name, sample in samples.items():
        assert SECRET_PATTERNS[name].search(sample), name


def test_no_env_files_or_dumps_shipped():
    bad = [p for p in list(DOCS.rglob("*")) + list(API.rglob("*"))
           if p.is_file() and (p.name.startswith(".env") or p.suffix in {".sql", ".db", ".sqlite", ".pem", ".key"})]
    assert not bad, [str(p.relative_to(ROOT)) for p in bad]


def test_no_external_scripts_styles_or_trackers():
    """Only plain outbound links to the repository are allowed; nothing is loaded from elsewhere."""
    allowed = ("https://github.com/Pranav240/paper-trading-agent",)
    for page in DOCS.glob("*.html"):
        parser = Refs()
        text = page.read_text(encoding="utf-8")
        parser.feed(text)
        for ref in parser.refs:
            if re.match(r"^(?:https?:)?//", ref, re.I):
                assert ref.startswith(allowed), f"{page.name}: external reference {ref}"
        assert not re.search(r"<(?:script|link|img|iframe)[^>]+(?:src|href)=[\"']https?://", text, re.I), page.name
    for asset in (DOCS / "assets").glob("*"):
        text = asset.read_text(encoding="utf-8").replace("https://github.com/Pranav240/paper-trading-agent", "")
        assert not re.search(r"@import|https?://", text, re.I), asset.name


# ------------------------------------------------------------------ schemas --

def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_snapshot_within_size_cap_and_manifest_consistent():
    manifest = load("manifest.json")
    total = sum(p.stat().st_size for p in DATA.rglob("*.json") if p.name != "manifest.json")
    assert total <= 20 * 1024 * 1024
    assert manifest["total_bytes"] == total
    index = load("decisions.json")
    assert index["count"] == len(index["decisions"]) == manifest["decisions"]
    assert len(list((DATA / "decisions").glob("*.json"))) == manifest["decisions"]
    assert sum(r["has_explanation"] for r in index["decisions"]) == manifest["with_explanation"]
    assert sum(r["has_trace"] for r in index["decisions"]) == manifest["with_trace"]


def test_decisions_index_schema():
    types = {"id": int, "backtest_id": int, "date": str, "symbol": str, "action": str, "confidence": float,
             "has_explanation": bool, "has_trace": bool}
    seen = set()
    for r in load("decisions.json")["decisions"]:
        for k, t in types.items():
            assert isinstance(r[k], t) or (t is float and isinstance(r[k], int)), (r["id"], k)
        assert r["action"] in {"BUY", "SELL", "HOLD"}
        assert r["var_effect"] in {None, "scaled", "blocked"}
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"])
        assert r["id"] not in seen
        seen.add(r["id"])


def test_every_decision_file_schema():
    index = {r["id"]: r for r in load("decisions.json")["decisions"]}
    for path in (DATA / "decisions").glob("*.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        assert path.stem == str(d["id"]) and d["id"] in index
        for k in ("backtest_id", "as_of", "symbol", "action", "reasoning", "agent_opinions", "risk_gate",
                  "var_forecast", "explanation", "trace"):
            assert k in d, (path.name, k)
        assert d["action"] == index[d["id"]]["action"]
        assert {o["agent_name"] for o in d["agent_opinions"]} <= {
            "technical_analyst", "sentiment_analyst", "portfolio_manager", "risk_manager"}
        assert (d["explanation"] is not None) == index[d["id"]]["has_explanation"]
        assert (d["trace"] is not None) == index[d["id"]]["has_trace"]
        if d["explanation"]:
            for k in ("summary", "drivers", "cited_headlines", "citations_valid", "template_baseline"):
                assert k in d["explanation"], (path.name, k)
            cited = {h["id"] for h in d["explanation"]["cited_headlines"]}
            for driver in d["explanation"]["drivers"]:
                assert set(driver["headline_ids"]) <= cited or not d["explanation"]["citations_valid"]
        if d["trace"]:
            assert d["trace"]["step_count"] == len(d["trace"]["steps"]) > 0
            for s in d["trace"]["steps"]:
                assert s["kind"] in {"llm", "retrieval", "check", "rule"}


def test_eval_schema_and_internal_arithmetic():
    e = load("eval.json")
    x = e["explanation_test"]["primary"]
    assert x["days"] == e["design"]["news_days"] == 40
    assert x["paired"]["explainer_wins"] + x["paired"]["quote_wins"] + x["paired"]["ties"] == x["days"]
    assert 0 <= x["explainer"]["matches_yes"] <= x["days"]
    r = e["retrieval_test"]["all_labelled_days"]
    assert r["paired"]["fts_wins"] + r["paired"]["recent_wins"] + r["paired"]["ties"] == r["days"]
    assert e["caveats"] and any("Claude" in c for c in e["caveats"])
    assert all(s for s in e["sources"])


def test_backtests_schema_and_honesty_fields():
    bts = load("backtests.json")["backtests"]
    real = [b for b in bts if b.get("results")]
    assert {b["id"] for b in real} == {45, 71, 136}
    for b in real:
        assert b["state"] in {"clean", "superseded"} and b["leak_status"]
        assert b["results"]["replay_drift"] < 0.01, "replay must reproduce the recorded run"
        for k in ("recorded_var_node", "replay_v1_rules", "buy_and_hold_20_shares"):
            assert isinstance(b["results"][k]["mtm_pnl"], float)
    assert next(b for b in real if b["id"] == 136)["state"] == "clean"
    assert all(b["state"] == "superseded" for b in real if b["id"] != 136)
    assert all(not b["published_decisions"] for b in bts if not b.get("results"))


def test_the_mixed_basis_headline_figure_is_not_published():
    """-63 (recorded V1 run on leaky data) vs +72 (replay on clean data) mixes bases."""
    for page in DOCS.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r"63\.10|−63|-\$?63|became \+\$72", text), page.name


# -------------------------------------------------------------------- links --

def resolve_local(page: Path, ref: str, site: Path):
    if re.match(r"^(?:[a-z][a-z0-9+.\-]*:|//|#)", ref, re.I):
        return None
    ref = ref.split("#")[0].split("?")[0]
    return (page.parent / ref).resolve() if ref else None


def test_every_internal_link_resolves(staged):
    missing = []
    for page in staged.rglob("*.html"):
        parser = Refs()
        parser.feed(page.read_text(encoding="utf-8", errors="replace"))
        for ref in parser.refs:
            target = resolve_local(page, ref, staged)
            if target is not None and not target.exists():
                missing.append(f"{page.relative_to(staged)} -> {ref}")
    assert not missing, "\n".join(missing)


def test_links_built_in_javascript_resolve(staged):
    """Pages build some links in JS strings; check every literal local file reference."""
    literal = re.compile(r"""["']((?:data/|assets/)[A-Za-z0-9_\-/]+\.(?:json|css|js)|[A-Za-z0-9_\-]+\.html)(?:\?[^"']*)?["']""")
    missing = []
    for page in staged.glob("*.html"):
        for m in literal.finditer(page.read_text(encoding="utf-8")):
            if not (staged / m.group(1)).exists():
                missing.append(f"{page.name}: {m.group(1)}")
    assert not missing, "\n".join(missing)


def test_every_decision_in_the_index_has_a_file(staged):
    for r in load("decisions.json")["decisions"]:
        assert (staged / "data" / "decisions" / f"{r['id']}.json").exists(), r["id"]


def test_existing_pages_are_still_served(staged):
    """Switching Pages to the Actions source must not take the old pages offline."""
    assert (staged / "dashboard" / "report.html").exists()
    assert (staged / "dashboard" / "backtest_dashboard.html").exists()
    assert (staged / "home-classic.html").exists()
    assert not list(staged.glob("*.md")), "markdown notes must not be published as raw files"


# ---------------------------------------------------------------------- API --

def test_every_api_route_is_get_only():
    from public_api.main import app

    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        assert methods <= {"GET", "HEAD", "OPTIONS"}, (route.path, methods)
    assert any(getattr(r, "path", "") == "/decisions/{decision_id}" for r in app.routes)


def test_api_rejects_writes_and_serves_the_snapshot():
    from fastapi.testclient import TestClient
    from public_api.main import app

    client = TestClient(app)
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/decisions").status_code in {404, 405}
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/decisions", params={"limit": 3}).json()["count"] > 3
    assert client.get("/decisions/2827").status_code == 200
    assert client.get("/decisions/2827/trace").json()["step_count"] == 7
    assert client.get("/decisions/1/trace").status_code == 404
    assert client.get("/decisions/2255/explanation").status_code == 404
    assert client.get("/decisions", params={"limit": 100000}).status_code == 422
    assert client.get("/eval").json()["design"]["news_days"] == 40


FORBIDDEN = ("anthropic", "openai", "langchain", "langchain_core", "langchain_anthropic", "langchain_openai",
             "langgraph", "psycopg", "psycopg_pool", "mcp", "alpaca", "dotenv", "app")


def test_api_package_imports_no_llm_client_or_database():
    for path in API.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in FORBIDDEN, f"{path.name} imports {n}"
    reqs = (API / "requirements.txt").read_text(encoding="utf-8").lower()
    for bad in ("anthropic", "openai", "langchain", "langgraph", "psycopg", "mcp"):
        assert bad not in reqs, bad


def test_api_loads_no_llm_client_at_runtime():
    code = (
        "import sys; sys.path.insert(0, %r); import public_api.main; "
        "bad = [m for m in sys.modules if m.split('.')[0] in %r]; "
        "print(','.join(sorted(bad)))" % (str(ROOT), FORBIDDEN)
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"loaded at runtime: {out.stdout.strip()}"
