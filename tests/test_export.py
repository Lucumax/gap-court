"""Clean public export: a whitelist copy with a fresh history, no remote, a secret scan and an internal-path scan."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gapcourt.export import MAX_QUOTE_WORDS, ExportBlocked, build_export, scan_text

ROOT = Path(__file__).resolve().parents[1]


def w(p: Path, text: str = "x\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def mini_repo(tmp_path) -> Path:
    src = tmp_path / "src_repo"
    for rel in ("README.md", "LICENSE", "NOTICE", "NOT_DONE.md", "pyproject.toml", ".gitignore", ".gitattributes"):
        w(src / rel, f"{rel}\n")
    w(src / "src/gapcourt/__init__.py", '__version__ = "0"\n')
    w(src / "tests/test_a.py", "def test_a():\n    pass\n")
    w(src / "tests/__pycache__/x.pyc", "binary")
    w(src / "scripts/walter_live_runs.ps1", "# no key\n")
    w(src / "scripts/walter_key_window.ps1", "# private helper\n")
    w(src / "corpus/global/G2.md", "---\ndoc_id: G2\nrepublish: yes\n---\nopen text\n")
    w(src / "corpus/SOURCES.json", "{}\n")
    w(src / "corpus_local/uae/U06.md", "---\ndoc_id: U06\nrepublish: link_only\n---\nLINK ONLY TEXT OF A PUBLISHER " + "x" * 80 + "\n")
    w(src / "build/kb/chunks.jsonl", '{"text": "LINK ONLY TEXT OF A PUBLISHER"}\n')
    w(src / "build/kb_public/KB_MANIFEST.json", "{}\n")
    w(src / "build/kb_public/chunks.jsonl", '{"text": null}\n')
    w(src / "docs/CORPUS_FORMAT.md", "format\n")
    w(src / "docs/SESSION_REPORT_2026-11-01.md", "internal\n")
    w(src / "runs_rehearsal/smoke/a/summary.json", "{}")
    w(src / ".env", "SECRET=1\n")
    w(src / "runs/smoke/S-03-live/summary.json", json.dumps({"cost": {"test_double": False}}))
    w(src / "runs/smoke/S-03-live/log.jsonl", "{}\n")
    w(src / "runs/smoke/S-03-double/summary.json", json.dumps({"cost": {"test_double": True}}))
    w(src / "runs/smoke/S-03-double/log.jsonl", "{}\n")
    return src


def test_export_copies_the_whitelist_and_nothing_private(tmp_path):
    src = mini_repo(tmp_path)
    out = tmp_path / "export"
    rep = build_export(src, out)
    have = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
    assert {"README.md", "LICENSE", "NOTICE", "src/gapcourt/__init__.py", "tests/test_a.py", "scripts/walter_live_runs.ps1",
            "corpus/global/G2.md", "corpus/SOURCES.json", "docs/CORPUS_FORMAT.md", "kb_release/KB_MANIFEST.json"} <= have
    for banned in ("corpus_local/uae/U06.md", "build/kb/chunks.jsonl", "scripts/walter_key_window.ps1", ".env",
                   "docs/SESSION_REPORT_2026-11-01.md", "runs_rehearsal/smoke/a/summary.json", "tests/__pycache__/x.pyc"):
        assert banned not in have, banned
    assert not any(h.startswith("build/") for h in have)
    assert rep["files"] == len(have)


def test_export_keeps_only_runs_that_called_the_model(tmp_path):
    src = mini_repo(tmp_path)
    out = tmp_path / "export"
    build_export(src, out)
    assert (out / "runs/smoke/S-03-live/summary.json").exists()
    assert not (out / "runs/smoke/S-03-double").exists()


def test_export_refuses_a_secret_and_never_prints_it(tmp_path):
    src = mini_repo(tmp_path)
    w(src / "docs/CORPUS_FORMAT.md", "key = sk-ant-api03-" + "A1b2C3d4" * 6 + "\n")
    with pytest.raises(ExportBlocked) as e:
        build_export(src, tmp_path / "export")
    msg = str(e.value)
    assert "docs/CORPUS_FORMAT.md" in msg and "secret" in msg and "A1b2C3d4" not in msg
    assert not (tmp_path / "export").exists()


def test_export_refuses_internal_paths_other_hackathons_and_emails(tmp_path):
    for bad, rule in (("path C:" + chr(92) + "Walter" + chr(92) + "x", "internal-path"), ("see the Meta entry", "other-entry"),
                      ("write to someone@example.com", "email")):
        src = mini_repo(tmp_path / rule)
        w(src / "docs/CORPUS_FORMAT.md", bad + "\n")
        with pytest.raises(ExportBlocked, match=rule):
            build_export(src, tmp_path / rule / "export")


def test_export_refuses_a_non_open_text_in_corpus(tmp_path):
    src = mini_repo(tmp_path)
    w(src / "corpus/global/G9.md", "---\ndoc_id: G9\nrepublish: link_only\n---\ntext\n")
    with pytest.raises(ExportBlocked, match="link_only"):
        build_export(src, tmp_path / "export")


def test_export_is_a_fresh_repository_without_a_remote_and_with_one_commit(tmp_path):
    src = mini_repo(tmp_path)
    out = tmp_path / "export"
    build_export(src, out, commit=True)
    assert subprocess.run(["git", "-C", str(out), "remote"], capture_output=True, text=True).stdout.strip() == ""
    log = subprocess.run(["git", "-C", str(out), "log", "--oneline"], capture_output=True, text=True).stdout.strip().splitlines()
    assert len(log) == 1
    assert subprocess.run(["git", "-C", str(out), "status", "--porcelain"], capture_output=True, text=True).stdout.strip() == ""


def test_scan_text_rules():
    assert scan_text("sk-ant-api03-" + "Z9y8X7w6" * 5, "f") and scan_text("ghp_" + "a" * 36, "f")
    assert scan_text("-----BEGIN RSA " + "PRIVATE KEY-----", "f")
    assert scan_text("api_" + "key = 'abcdefghijklmnopqrstuvwxyz123456'", "f")
    assert scan_text("ANTHROPIC_API_KEY is read from the environment", "f") == []
    assert scan_text("sha256: " + "a" * 64, "f") == []


def _release_export():
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_export", ROOT / "scripts" / "release_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_real_repo_export_has_no_internal_paths_no_link_only_text_and_passes_the_lint(tmp_path):
    # the release path: the unchanged exporter run on a staged copy without the files of config/export_withheld.json
    out = tmp_path / "export"
    rel = _release_export()
    rep = rel.release_export(ROOT, out)
    assert rep["files"] > 50
    for path in rep["withheld"]:
        assert not (out / path).exists() and (out / path).parent.joinpath("WITHHELD.md").is_file()
    assert not any(p.name.upper().startswith("INTERNAL") for p in out.rglob("*"))
    local = ROOT / "corpus_local"
    if local.exists():
        needles = set()
        for p in local.rglob("*.md"):
            if p.parent.name.startswith("_"):
                continue
            body = p.read_text(encoding="utf-8").split(chr(10) + "---" + chr(10), 1)[-1]  # front matter is bibliographic metadata
            needles |= {l.strip() for l in body.splitlines()
                         if len(l.strip()) > 70 and l.strip()[-1] in ".;:,"}  # sentences, not instrument titles
        for f in out.rglob("*"):
            if f.is_file() and f.suffix in (".md", ".json", ".jsonl", ".py", ".txt"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                leaked = [n for n in needles if n in text]
                if f.relative_to(out).parts[0] == "runs":
                    # A live run's gate record quotes the words the model cited, by design (a mechanical quotation check needs
                    # them): one source line of up to MAX_QUOTE_WORDS words is a verification quotation, the same rule the export
                    # enforces everywhere (build_export refuses a longer run of link-only text). A longer line is a leak.
                    leaked = [n for n in leaked if len(n.split()) > MAX_QUOTE_WORDS]
                assert not leaked, f"{f.relative_to(out)} contains link-only text"
    lint = subprocess.run(["python", str(ROOT / "scripts" / "repo_lint.py"), "--root", str(out)], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout[-800:]


# ---- Review fixes: scanner and whitelist ----------------------------------------

@pytest.mark.parametrize("rel", ["docs/.env.local", "config/private.key", "docs/credentials.json", "scripts/id_rsa", "config/server.pem",
                                 "docs/my_secrets.txt"])
def test_export_refuses_credential_looking_file_names_even_with_innocent_content(tmp_path, rel):
    src = mini_repo(tmp_path)
    w(src / rel, "nothing here\n")
    with pytest.raises(ExportBlocked, match="credential-file"):
        build_export(src, tmp_path / "export")


def test_export_refuses_file_types_it_cannot_scan(tmp_path):
    src = mini_repo(tmp_path)
    w(src / "docs/notes.pdf", "%PDF-1.4 binary\n")
    with pytest.raises(ExportBlocked, match="unscanned-file-type"):
        build_export(src, tmp_path / "export")


@pytest.mark.parametrize("text,rule", [
    ("see C:/Walter/private/x", "internal-path"),
    ("see /home/alice/work", "internal-path"),
    ("write to person@itu.int.evil.example", "email"),
    ("write to person@anthropic.com.evil.io", "email"),
])
def test_scan_text_catches_slash_paths_home_paths_and_lookalike_domains(text, rule):
    assert [f["rule"] for f in scan_text(text, "docs/x.md")] == [rule]


def test_scan_text_still_allows_the_named_domains():
    assert scan_text("noreply@users.noreply.github.com and someone@itu.int", "docs/x.md") == []


def test_test_export_file_is_not_exempt_from_secret_detection():
    from gapcourt.export import EXEMPT

    assert "secret" not in EXEMPT.get("tests/test_export.py", set())
    assert "secret" not in EXEMPT.get("src/gapcourt/export.py", set())
    assert scan_text("ghp_" + "a" * 36, "tests/test_export.py")


def _link_only_repo(tmp_path, words=60):
    src = mini_repo(tmp_path)
    body = " ".join(f"w{i}x" for i in range(words))
    w(src / "corpus_local/uae/U06.md", "---\ndoc_id: U06\nrepublish: link_only\n---\n" + body + ".\n")
    return src, body


def test_export_refuses_a_passage_length_copy_of_link_only_text(tmp_path):
    src, body = _link_only_repo(tmp_path, 60)
    w(src / "dossiers/D1.md", "A reviewer note.\n\n> " + body + "\n")
    with pytest.raises(ExportBlocked, match="restricted-passage"):
        build_export(src, tmp_path / "export")


def test_export_allows_a_short_verification_quotation_and_reports_it(tmp_path):
    src, body = _link_only_repo(tmp_path, 60)
    short = " ".join(body.split()[:12])
    w(src / "dossiers/D1.md", f'The adviser quoted "{short}" from the instrument.\n')
    rep = build_export(src, tmp_path / "export")
    assert rep["quotations"] == {"dossiers/D1.md": {"runs": 1, "longest_words": 12}}


def test_export_reports_unfilled_walter_placeholders_without_blocking(tmp_path):
    src = mini_repo(tmp_path)
    w(src / "docs/CORPUS_FORMAT.md", "Contact: [WALTER: email]\n")
    rep = build_export(src, tmp_path / "export")
    assert rep["placeholders"] == {"docs/CORPUS_FORMAT.md": 1}


def test_export_command_prints_quotations_and_placeholders(tmp_path, capsys):
    from gapcourt.cli import main

    src, body = _link_only_repo(tmp_path, 60)
    short = " ".join(body.split()[:12])
    w(src / "dossiers/D1.md", f'The adviser quoted "{short}". Contact: [WALTER: email]\n')
    assert main(["export-public", "--src", str(src), "--out", str(tmp_path / "export")]) == 0
    out = capsys.readouterr().out
    assert "dossiers/D1.md" in out and "12 words" in out and "[WALTER: ...]" in out


# ---- first live smoke session (2026-11-02): live runs quote the cited words in their gate records ------------------------

def _live_run_with(src, text, name="S-03-live"):
    w(src / f"runs/smoke/{name}/summary.json", json.dumps({"cost": {"test_double": False}}))
    w(src / f"runs/smoke/{name}/log.jsonl", text + chr(10))


def test_export_allows_a_short_verification_quote_in_a_live_run_log_and_reports_it(tmp_path):
    src, body = _link_only_repo(tmp_path, 60)
    short = " ".join(body.split()[:24])
    _live_run_with(src, json.dumps({"gate": {"scope_findings": [{"quote": short}]}}))
    rep = build_export(src, tmp_path / "export")
    assert rep["quotations"]["runs/smoke/S-03-live/log.jsonl"] == {"runs": 1, "longest_words": 24}


def test_export_refuses_a_passage_length_copy_of_link_only_text_in_a_live_run_log(tmp_path):
    src, body = _link_only_repo(tmp_path, 60)
    _live_run_with(src, json.dumps({"passage_text": body}))
    with pytest.raises(ExportBlocked, match="restricted-passage"):
        build_export(src, tmp_path / "export")


def test_export_commit_uses_the_public_identity_by_default_and_changes_no_git_config(tmp_path):
    from gapcourt.export import EXPORT_EMAIL, EXPORT_NAME

    assert (EXPORT_NAME, EXPORT_EMAIL) == ("Lucumax", "242802005+Lucumax@users.noreply.github.com")
    src = mini_repo(tmp_path)
    out = tmp_path / "export"
    build_export(src, out, commit=True)
    who = subprocess.run(["git", "-C", str(out), "log", "-1", "--format=%an <%ae>|%cn <%ce>"], capture_output=True, text=True).stdout.strip()
    ident = "Lucumax <242802005+Lucumax@users.noreply.github.com>"
    assert who == f"{ident}|{ident}"
    for key in ("user.name", "user.email"):  # the identity travels in the environment of the commit, not in a config file
        got = subprocess.run(["git", "-C", str(out), "config", "--local", "--get", key], capture_output=True, text=True)
        assert got.stdout.strip() == ""


def test_the_licence_names_the_author_only():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Walter Guevara" + chr(10) in text and "Juncture" not in text


def test_committed_export_contains_the_live_runs_even_though_gitignore_lists_runs(tmp_path):
    # the real .gitignore ignores runs/; `git add -A` alone left the saved live runs out of the exported commit (2026-11-02)
    src = mini_repo(tmp_path)
    w(src / ".gitignore", "runs/" + chr(10) + "build/" + chr(10))
    out = tmp_path / "export"
    build_export(src, out, commit=True)
    tracked = subprocess.run(["git", "-C", str(out), "ls-files"], capture_output=True, text=True).stdout.split()
    assert "runs/smoke/S-03-live/summary.json" in tracked and "runs/smoke/S-03-live/log.jsonl" in tracked
    assert not any(x.startswith("runs/smoke/S-03-double") for x in tracked)
    assert subprocess.run(["git", "-C", str(out), "status", "--porcelain"], capture_output=True, text=True).stdout.strip() == ""


def test_the_rendered_report_is_a_submission_file_and_stays_out_of_the_public_export(tmp_path):
    src = mini_repo(tmp_path)
    (src / "report").mkdir(exist_ok=True)
    (src / "report/REPORT_v1.docx").write_bytes(b"PK\x03\x04 binary word file")
    (src / "report/REPORT_v1.pdf").write_bytes(b"%PDF binary")
    w(src / "report/TEMPLATE_V3_FILLABLE.md", "template\n")
    out = tmp_path / "export"
    build_export(src, out)
    assert not (out / "report/REPORT_v1.docx").exists() and not (out / "report/REPORT_v1.pdf").exists()
    assert (out / "report/TEMPLATE_V3_FILLABLE.md").exists()


# ---- private helpers and internal notes never ship ------------------------------------

def test_export_contains_no_internal_notes_or_private_helpers(tmp_path):
    src = mini_repo(tmp_path)
    for rel in ("docs/INTERNAL_NOTE_A.md", "docs/INTERNAL_X/plan.md", "report/INTERNAL_CHECK.md", "evals/INTERNAL_labels_notes.md",
                "scripts/walter_card.ps1", "scripts/walter_send.ps1", "scripts/walter_helper.py", "scripts/private_helper.ps1", "scripts/key_window.ps1"):
        w(src / rel, "private\n")
    out = tmp_path / "export"
    build_export(src, out)
    have = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
    assert not [h for h in have if "INTERNAL" in h.upper().split("/")[-1] or "/INTERNAL" in h.upper()], have
    assert not [h for h in have if h.startswith("scripts/walter_") and h.endswith(".ps1") and h != "scripts/walter_live_runs.ps1"], have
    assert "scripts/walter_helper.py" in have  # only the private PowerShell helpers are dropped, not Python scripts
    assert not [h for h in have if h.startswith("scripts/") and ("key_window" in h or "private" in h)], have
    assert "scripts/walter_live_runs.ps1" in have  # the documented live-run script is part of the entry


def test_raw_export_of_the_real_repo_is_blocked_only_by_the_withheld_files(tmp_path):
    """Withholding is narrow: without the staging step, every finding of the exporter is in a listed file (a new leak in any
    other file still fails here), and every listed file still has the hash recorded for it."""
    rel = _release_export()
    entries = rel.check_withheld(ROOT, rel.load_withheld(ROOT))
    listed = {e["path"] for e in entries}
    try:
        build_export(ROOT, tmp_path / "raw")
    except ExportBlocked as exc:
        found = {line.split(":")[0] for line in str(exc).splitlines()[1:] if line.strip()}
        assert found and found <= listed, found - listed


def test_release_export_refuses_a_withheld_file_whose_hash_changed(tmp_path):
    rel = _release_export()
    src = mini_repo(tmp_path)
    w(src / "config/export_withheld.json", json.dumps({"withheld": [{"path": "runs/smoke/S-03-live/log.jsonl", "sha256": "0" * 64, "reason": "test"}]}))
    with pytest.raises(rel.WithheldMismatch):
        rel.release_export(src, tmp_path / "export")
