"""Acquisition manifest for the link-only sources (corpus/SOURCES.json) and `gapcourt kb-fetch`.

The manifest lists, for each raw file behind a corpus document: the official address, the expected
sha256 and size, and where to save it. It is a tested acquisition workflow, not a promise that every
host serves scripts: entries marked `access: manual` are hosts that refused an automated client or
files not yet obtained, and the command prints the browser step for them instead of requesting them.

Rules: a plain user agent, no cookies, no workaround on a refusal. A hash mismatch stops the run and
names the file; nothing mismatched is written and an existing local file is never overwritten.
Downloaded files are data: they are saved and hashed, never opened or executed here.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import __version__

USER_AGENT = f"gapcourt-kb-fetch/{__version__} (hackathon knowledge-base rebuild; plain client)"
ACCESS = ("automatic", "manual")
MEDIA = ("pdf", "html", "docx", "zip", "json", "txt")
BLOCK_STATUS = (401, 403, 406, 429, 503)
# Hosts that refused an automated client during collection (corpus_local/_probe/fetch_log_2026-10-27.tsv and
# source front matter). Entries on these hosts must be manual.
KNOWN_BLOCKING_HOSTS = ("uaelegislation.gov.ae", "ai.gov.ae", "uaecabinet.ae", "uaemodel.egsep.ae",
                        "eur-lex.europa.eu", "www.coe.int")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    pass


@dataclass
class FetchResult:
    doc_id: str
    file: str
    status: str
    message: str
    sha256: str | None = None


def load_manifest(path: str | Path) -> dict:
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    errs = validate_manifest(m)
    if errs:
        raise ManifestError("; ".join(errs))
    return m


def validate_manifest(m: dict) -> list[str]:
    errs: list[str] = []
    if m.get("manifest_version") != 1:
        errs.append("manifest_version must be 1")
    seen = set()
    for i, e in enumerate(m.get("sources") or []):
        tag = f"source {i} ({e.get('doc_id')})"
        for k in ("doc_id", "file", "url", "access", "media"):
            if not e.get(k):
                errs.append(f"{tag}: missing {k}")
        if e.get("access") not in ACCESS:
            errs.append(f"{tag}: access must be one of {ACCESS}")
        if e.get("media") and e.get("media") not in MEDIA:
            errs.append(f"{tag}: media must be one of {MEDIA}")
        f = str(e.get("file", ""))
        pp = PurePosixPath(f)
        if pp.is_absolute() or ".." in pp.parts or "\\" in f or ":" in f:
            errs.append(f"{tag}: file must be a relative path inside the repository")
        if f in seen:
            errs.append(f"{tag}: duplicate file {f}")
        seen.add(f)
        h = e.get("sha256")
        if h is not None and not _HEX64.match(str(h)):
            errs.append(f"{tag}: sha256 must be 64 lowercase hex characters or null")
        if e.get("access") == "automatic" and h is None:
            errs.append(f"{tag}: an automatic source needs an expected sha256")
        if e.get("access") == "manual" and not e.get("manual_reason"):
            errs.append(f"{tag}: a manual source needs manual_reason")
        if not str(e.get("url", "")).startswith(("https://", "http://")):
            errs.append(f"{tag}: url must be http(s)")
    if not m.get("sources"):
        errs.append("no sources")
    return errs


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _manual_step(e: dict) -> str:
    return f"open {e['url']} in a browser, save to {e['file']}, then rerun"


def _looks_like_challenge(e: dict, resp) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    return e["media"] not in ("html", "txt", "json") and ctype.startswith("text/html")


def fetch_sources(manifest: dict, root: str | Path, client=None, only: list[str] | None = None,
                  check_only: bool = False, timeout: float = 60.0, keep_going: bool = False) -> list[FetchResult]:
    """`keep_going` reports every mismatch instead of stopping at the first (diagnosis only; still writes nothing
    mismatched)."""
    root = Path(root)
    own_client = None
    out: list[FetchResult] = []
    try:
        for e in manifest["sources"]:
            if only and e["doc_id"] not in only:
                continue
            target = root / e["file"]
            want = e.get("sha256")
            if target.exists():
                got = _sha(target.read_bytes())
                if want is None:
                    out.append(FetchResult(e["doc_id"], e["file"], "pending_hash",
                                           f"file present with sha256 {got}; review the file, then record this hash "
                                           "in corpus/SOURCES.json", got))
                    continue
                if got != want:
                    out.append(FetchResult(e["doc_id"], e["file"], "hash_mismatch",
                                           f"local file {e['file']} differs from the manifest (expected {want}, found "
                                           f"{got}); stopping; move it aside or check the source", got))
                    if keep_going:
                        continue
                    break
                status = "manual_present_ok" if e["access"] == "manual" else "present_ok"
                out.append(FetchResult(e["doc_id"], e["file"], status, "present, sha256 matches", got))
                continue
            if e["access"] == "manual":
                out.append(FetchResult(e["doc_id"], e["file"], "manual_needed",
                                       _manual_step(e) + f" ({e['manual_reason']})"))
                continue
            if check_only:
                out.append(FetchResult(e["doc_id"], e["file"], "missing", "not present; run kb-fetch without --check-only"))
                continue
            if client is None:
                import httpx

                own_client = client = httpx.Client(timeout=timeout, follow_redirects=True,
                                                   headers={"User-Agent": USER_AGENT})
            try:
                resp = client.get(e["url"], headers={"User-Agent": USER_AGENT}, follow_redirects=True)
            except Exception as exc:  # network failure: report, keep going
                out.append(FetchResult(e["doc_id"], e["file"], "error",
                                       f"{type(exc).__name__}: {exc}; " + _manual_step(e)))
                continue
            if resp.status_code in BLOCK_STATUS or (resp.status_code == 200 and _looks_like_challenge(e, resp)):
                out.append(FetchResult(e["doc_id"], e["file"], "blocked", _manual_step(e)))
                continue
            if resp.status_code != 200:
                out.append(FetchResult(e["doc_id"], e["file"], "unavailable",
                                       f"HTTP {resp.status_code}; the source may have moved; " + _manual_step(e)))
                continue
            body = resp.content
            got = _sha(body)
            if got != want:
                out.append(FetchResult(e["doc_id"], e["file"], "hash_mismatch",
                                       f"downloaded bytes for {e['file']} differ from the manifest (expected {want}, got "
                                       f"{got}, {len(body)} bytes); stopping; nothing was written", got))
                if keep_going:
                    continue
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(body)
            tmp.replace(target)
            out.append(FetchResult(e["doc_id"], e["file"], "fetched_ok", f"saved {len(body)} bytes, sha256 matches", got))
    finally:
        if own_client is not None:
            own_client.close()
    return out


OK_STATUSES = ("present_ok", "fetched_ok", "manual_present_ok")
