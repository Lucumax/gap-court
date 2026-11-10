"""K1 acquisition manifest with hash checks, and K2 reproducibility check of the corpus.

Network calls go through httpx.MockTransport; nothing here touches a real host.
"""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from gapcourt.fetch import KNOWN_BLOCKING_HOSTS, fetch_sources, load_manifest, validate_manifest
from gapcourt.prepare import RECIPES, Recipe, check_corpus

ROOT = Path(__file__).resolve().parents[1]
PDF = b"%PDF-1.7 synthetic test bytes"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def manifest(*entries):
    return {"manifest_version": 1, "sources": list(entries)}


def entry(**kw):
    e = {"doc_id": "X01", "file": "corpus_local/_raw/X01/x.pdf", "url": "https://example.invalid/x.pdf",
         "sha256": sha(PDF), "bytes": len(PDF), "media": "pdf", "access": "automatic", "note": "synthetic"}
    e.update(kw)
    return e


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---- fetch ------------------------------------------------------------------

def test_fetch_writes_verified_file_then_skips_present_ok(tmp_path):
    calls = []

    def h(req):
        calls.append(str(req.url))
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    res = fetch_sources(manifest(entry()), tmp_path, client=client_for(h))
    assert [r.status for r in res] == ["fetched_ok"]
    assert (tmp_path / "corpus_local/_raw/X01/x.pdf").read_bytes() == PDF
    res2 = fetch_sources(manifest(entry()), tmp_path, client=client_for(h))
    assert [r.status for r in res2] == ["present_ok"] and len(calls) == 1


def test_fetch_rejects_hash_mismatch(tmp_path):
    def h(req):
        return httpx.Response(200, content=b"%PDF different bytes", headers={"content-type": "application/pdf"})

    second = entry(doc_id="X02", file="corpus_local/_raw/X02/y.pdf")
    res = fetch_sources(manifest(entry(), second), tmp_path, client=client_for(h))
    assert res[0].status == "hash_mismatch"
    assert "corpus_local/_raw/X01/x.pdf" in res[0].message and "stopping" in res[0].message
    assert not (tmp_path / "corpus_local/_raw/X01/x.pdf").exists()  # nothing written
    assert len(res) == 1  # stopped: the second source was not attempted


def test_fetch_stops_on_a_local_file_that_differs(tmp_path):
    p = tmp_path / "corpus_local/_raw/X01/x.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"edited")
    res = fetch_sources(manifest(entry()), tmp_path, client=client_for(lambda r: httpx.Response(500)))
    assert res[0].status == "hash_mismatch" and "local file" in res[0].message
    assert p.read_bytes() == b"edited"  # never overwritten


def test_fetch_reports_blocked_host_with_manual_step(tmp_path):
    res = fetch_sources(manifest(entry()), tmp_path, client=client_for(lambda r: httpx.Response(403, text="denied")))
    r = res[0]
    assert r.status == "blocked"
    assert r.message.startswith("open https://example.invalid/x.pdf in a browser, save to ")
    assert r.message.endswith("then rerun")
    assert "corpus_local" in r.message


def test_fetch_html_challenge_on_pdf_source_is_blocked(tmp_path):
    def h(req):
        return httpx.Response(200, text="<html>checking your browser</html>", headers={"content-type": "text/html"})

    assert fetch_sources(manifest(entry()), tmp_path, client=client_for(h))[0].status == "blocked"


def test_fetch_sends_a_plain_user_agent_and_no_workaround_headers(tmp_path):
    seen = {}

    def h(req):
        seen.update(req.headers)
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    fetch_sources(manifest(entry()), tmp_path, client=client_for(h))
    assert seen["user-agent"].startswith("gapcourt-kb-fetch/")
    assert "cookie" not in seen and "authorization" not in seen


def test_manual_entries_are_never_fetched_and_say_so(tmp_path):
    def h(req):
        raise AssertionError("a manual-access source must not be requested")

    e = entry(access="manual", manual_reason="host refuses scripted clients (HTTP 403 on 2026-10-27)")
    r = fetch_sources(manifest(e), tmp_path, client=client_for(h))[0]
    assert r.status == "manual_needed" and "open https://example.invalid/x.pdf in a browser" in r.message
    p = tmp_path / e["file"]
    p.parent.mkdir(parents=True)
    p.write_bytes(PDF)
    assert fetch_sources(manifest(e), tmp_path, client=client_for(h))[0].status == "manual_present_ok"


def test_pending_entry_without_hash_asks_for_review_of_the_recorded_hash(tmp_path):
    e = entry(access="manual", manual_reason="not yet downloaded", sha256=None, bytes=None)
    p = tmp_path / e["file"]
    p.parent.mkdir(parents=True)
    p.write_bytes(PDF)
    r = fetch_sources(manifest(e), tmp_path, client=client_for(lambda q: httpx.Response(500)))[0]
    assert r.status == "pending_hash" and sha(PDF) in r.message


def test_check_only_makes_no_network_call(tmp_path):
    def h(req):
        raise AssertionError("check-only must not use the network")

    r = fetch_sources(manifest(entry()), tmp_path, client=client_for(h), check_only=True)[0]
    assert r.status == "missing"


def test_validate_manifest_requires_reasons_hashes_and_known_access(tmp_path):
    assert validate_manifest(manifest(entry())) == []
    assert validate_manifest(manifest(entry(access="manual")))  # manual without a reason
    assert validate_manifest(manifest(entry(sha256=None)))  # automatic without a hash
    assert validate_manifest(manifest(entry(access="sometimes")))
    assert validate_manifest(manifest(entry(file="../outside.pdf")))


# ---- the committed manifest -------------------------------------------------

def test_repo_manifest_is_valid_and_marks_manual_access_sources():
    m = load_manifest(ROOT / "corpus" / "SOURCES.json")
    assert validate_manifest(m) == []
    by = {(e["doc_id"], e["file"]): e for e in m["sources"]}
    pending = {e["doc_id"] for e in m["sources"] if e["sha256"] is None}
    assert {"U04", "U10"} <= pending and "U01EN" not in pending
    for e in m["sources"]:
        host = httpx.URL(e["url"]).host
        if host in KNOWN_BLOCKING_HOSTS:
            assert e["access"] == "manual", e["doc_id"]
        if e["access"] == "manual":
            assert e["manual_reason"]
    assert by and all(e["file"].startswith("corpus_local/") for e in m["sources"])


@pytest.mark.skipif(not (ROOT / "corpus_local" / "uae" / "U06.md").exists(), reason="link-only corpus not present")
def test_repo_manifest_covers_every_local_corpus_document():
    from gapcourt.corpus import load_document

    m = load_manifest(ROOT / "corpus" / "SOURCES.json")
    hashes = {e["sha256"] for e in m["sources"]}
    for p in sorted((ROOT / "corpus_local").glob("*/*.md")):
        if p.parent.name.startswith("_"):
            continue
        doc = load_document(p)
        assert doc.meta["sha256"] in hashes, p.name


# ---- prepare ----------------------------------------------------------------

def _corpus(tmp_path, body="body line one\nbody line two\n"):
    raw = tmp_path / "corpus_local/_raw/X01/x.txt"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(body.encode("utf-8"))

    def build(raw_path):
        return "---\ndoc_id: X01\nsha256: " + sha(Path(raw_path).read_bytes()) + "\n---\n" + Path(raw_path).read_text(encoding="utf-8")

    out = tmp_path / "corpus_local/uae/X01.md"
    out.parent.mkdir(parents=True)
    out.write_text(build(raw), encoding="utf-8", newline="\n")
    return raw, out, {"X01": Recipe("X01", build, "synthetic recipe")}


def test_prepare_reproduces_committed_hashes(tmp_path):
    raw, out, recipes = _corpus(tmp_path)
    res = check_corpus(tmp_path, recipes=recipes)
    assert [(r.doc_id, r.status) for r in res] == [("X01", "REPRODUCED")]


def test_prepare_flags_a_hand_edited_file(tmp_path):
    raw, out, recipes = _corpus(tmp_path)
    out.write_text(out.read_text(encoding="utf-8").replace("line two", "line 2"), encoding="utf-8", newline="\n")
    r = check_corpus(tmp_path, recipes=recipes)[0]
    assert r.status == "DIFFERS" and "line" in r.detail


def test_prepare_flags_missing_recipe_and_missing_raw(tmp_path):
    raw, out, recipes = _corpus(tmp_path)
    assert check_corpus(tmp_path, recipes={}, audit=False)[0].status == "NO_RECIPE"
    assert check_corpus(tmp_path, recipes={})[0].status == "AUDITED"  # no recipe, but every line is verbatim in the raw file
    raw.unlink()
    assert check_corpus(tmp_path, recipes=recipes)[0].status == "RAW_MISSING"


def test_prepare_reports_recipe_errors_instead_of_passing(tmp_path):
    raw, out, _ = _corpus(tmp_path)

    def broken(raw_path):
        raise ImportError("pypdfium2 not installed")

    assert check_corpus(tmp_path, recipes={"X01": Recipe("X01", broken, "x")})[0].status == "RECIPE_ERROR"


@pytest.mark.skipif(not (ROOT / "corpus_local" / "uae" / "U01.md").exists(), reason="link-only corpus not present")
def test_real_corpus_u01_reproduces_with_its_registered_recipe():
    res = {r.doc_id: r for r in check_corpus(ROOT)}
    assert res["U01"].status == "REPRODUCED"
    assert "U01" in RECIPES


def test_keep_going_reports_every_mismatch_and_still_writes_nothing(tmp_path):
    def h(req):
        return httpx.Response(200, content=b"%PDF other", headers={"content-type": "application/pdf"})

    second = entry(doc_id="X02", file="corpus_local/_raw/X02/y.pdf")
    res = fetch_sources(manifest(entry(), second), tmp_path, client=client_for(h), keep_going=True)
    assert [r.status for r in res] == ["hash_mismatch", "hash_mismatch"]
    assert not any((tmp_path / e).exists() for e in ("corpus_local/_raw/X01/x.pdf", "corpus_local/_raw/X02/y.pdf"))


def test_kb_fetch_command_exit_codes(tmp_path, capsys):
    from gapcourt.cli import main

    m = tmp_path / "SOURCES.json"
    m.write_text(json.dumps(manifest(entry())), encoding="utf-8")
    assert main(["kb-fetch", "--manifest", str(m), "--root", str(tmp_path), "--check-only"]) == 2
    p = tmp_path / "corpus_local/_raw/X01/x.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(PDF)
    assert main(["kb-fetch", "--manifest", str(m), "--root", str(tmp_path), "--check-only"]) == 0
    p.write_bytes(b"changed")
    assert main(["kb-fetch", "--manifest", str(m), "--root", str(tmp_path), "--check-only"]) == 3
    assert "1 of 1" not in capsys.readouterr().out.splitlines()[-1]


def test_pyproject_declares_the_packages_that_prepare_needs():
    # prepare imports PyMuPDF, BeautifulSoup and lxml; a fresh clone must be able to install them
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    extra = " ".join(data["project"]["optional-dependencies"].get("prepare", [])).lower()
    for pkg in ("pymupdf", "beautifulsoup4", "lxml", "pypdfium2"):
        assert pkg in extra, pkg
