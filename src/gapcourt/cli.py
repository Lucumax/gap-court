"""Command line: build-kb, search, coverage, verify-log, run-scenario, eval-retrieval."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .audit import AuditLog, verify_records
from .chunking import ChunkParams
from .corpus import CorpusError
from .embed import HashingEmbedder
from .index import KB, build_kb
from .retrieve import HybridRetriever


def _cmd_build(a) -> int:
    roots = [Path(r) for r in a.root]
    pub = [Path(p) for p in (a.public_root or [])]
    if a.embedder == "ollama":
        from . import itu_adapter as itu

        embedder = itu.ReferenceOllamaEmbedder(model=a.ollama_model, base_url=a.ollama_url)
    else:
        embedder = HashingEmbedder(dim=a.dim)
    try:
        kb = build_kb(roots, embedder, ChunkParams(max_chars=a.max_chars), public_roots=pub)
    except CorpusError as e:
        raise SystemExit(f"error: {e}")
    except Exception as e:  # an embedder that cannot reach its server must not look like a corpus problem
        if type(e).__name__ == "EmbedderUnavailable":
            raise SystemExit(f"error: {e}")
        raise
    kb.save(a.out)
    if a.public_out:
        kb.save_public(a.public_out)
    print(f"{kb.version}  chunks={len(kb.entries)}  collections={sorted(kb.collections)}  embedder={kb.embedder.name}")
    if a.chroma_dir:
        from . import itu_adapter as itu

        store = itu.ChromaStore(a.chroma_dir)
        counts = itu.load_kb_into_store(kb, store, use_kb_vectors=True)
        print("chroma store", a.chroma_dir, counts)
    return 0


def _cmd_expand_query(a) -> int:
    from . import bilingual as bl

    e = bl.expand_query(a.query)
    print(f"map sha256 {bl.map_sha256()} ({len(bl.CONCEPTS)} concepts, key {bl.KEY_NAME})")
    if not e.concepts:
        print("no expansion: no concept in the map matches this question")
        return 0
    print("concepts: " + ", ".join(e.concepts))
    print("Arabic search terms added to the lexical arm (a retrieval aid, not a translation): " + " | ".join(e.terms))
    return 0


def _cmd_dossier_check(a) -> int:
    from .dossier import evaluate_spec, render_markdown

    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    audit = json.loads(Path(a.audit).read_text(encoding="utf-8")) if a.audit and Path(a.audit).exists() else None
    res = evaluate_spec(KB.load(a.kb), spec, audit=audit, repo_root=a.repo_root)
    if a.out_md:
        Path(a.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out_md).write_text(render_markdown(spec, res), encoding="utf-8", newline=chr(10))
    if a.out_json:
        Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out_json).write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8", newline=chr(10))
    n = res["counts"]
    print(f"quotations verified: {n['claims_verified']} of {n['claims']}; findings supported: {n['findings_supported']} of {n['findings']}")
    flagged = {k: v for k, v in res["findings"].items() if v["status"] != "supported"}
    for k, v in flagged.items():
        print(f"flagged {k}: {v['reason']}")
    declared = set(spec.get("expected_flagged", []))
    if a.strict and (n["claims_verified"] != n["claims"] or set(flagged) != declared):
        print("strict: a quotation did not verify or a flagged finding is not declared in expected_flagged", file=sys.stderr)
        return 1
    return 0


def _cmd_search(a) -> int:
    kb = KB.load(a.kb)
    hits = HybridRetriever(kb, a.collections.split(","), k=a.k, mode=a.mode).search(a.query)
    for h in hits:
        print(f"{h.score:.4f}  {h.chunk.cite}  [{h.chunk.chunk_id}]  p.{h.chunk.page_start}  {h.chunk.text[:100]!r}")
    return 0


def _cmd_coverage(a) -> int:
    kb = KB.load(a.kb)
    print("doc_id  collection  lang  sections  pages_seen  chunks  oversize  republish")
    for r in kb.coverage_table():
        print(f"{r['doc_id']}  {r['collection']}  {r['language']}  {r['sections']}  {r['pages_seen']}  "
              f"{r['chunks']}  {r['oversize_chunks']}  {r['republish']}")
    return 0


def _cmd_verify(a) -> int:
    recs = AuditLog.load_records(a.log)
    expected = json.loads(Path(a.digest).read_text(encoding="utf-8")) if a.digest else None
    res = verify_records(recs, expected=expected)
    if res.ok:
        print(f"internal consistency verified: {len(recs)} records"
              + (" match the retained digest" if expected else " (no retained digest supplied; truncation not detectable)"))
        sj = Path(a.log).parent / "summary.json"
        if sj.exists():
            from .audit import check_summary_binding

            why = check_summary_binding(recs, json.loads(sj.read_text(encoding="utf-8")), expected, Path(a.log).parent)
            if why:
                print(f"summary check FAILED: {why}")
                return 1
            print("summary.json is bound into the digest (every field, and the sidecar files the log names)")
        return 0
    print("verification FAILED")
    for e in res.errors:
        print(" -", e)
    return 1


def _code_version() -> str:
    import subprocess

    here = Path(__file__).resolve().parent
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             check=True, cwd=here).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                               text=True, check=True, cwd=here).stdout.strip()
        return (sha + ("+dirty" if dirty else "")) or "unversioned"
    except Exception:
        return "unversioned"


TEST_DOUBLE_BANNER = "TEST DOUBLE RUN: no model was called; output is not model output and is not a result"


def _backend(a):
    """One AnthropicBackend (and one cost ledger) shared by every role in the run."""
    from .llm import (JOURNAL_DIR, AnthropicBackend, CostLedger, ModelSettings, TestDoubleAnthropicClient,
                      prior_spend_usd)

    s = ModelSettings.load(a.config)
    prior = 0.0 if a.test_double else prior_spend_usd(a.runs_root)
    journal = None if a.test_double else Path(a.runs_root) / JOURNAL_DIR
    ledger = CostLedger(s.prices, s.run_cap_usd, s.total_cap_usd, prior_spent_usd=prior, journal_dir=journal)
    b = AnthropicBackend(s, ledger, client=TestDoubleAnthropicClient() if a.test_double else None)
    if not a.test_double:
        b._get_client()  # fails here, before any folder is written, if the key or SDK is missing
    ledger.check(s.model_id, 0.0)  # an unknown price or an exhausted total cap fails here too
    return b


def _backend_or_exit(a):
    from .llm import BackendNotConfigured, RunCapReached

    try:
        return _backend(a)
    except (BackendNotConfigured, RunCapReached) as e:
        print(f"error: {e}", file=sys.stderr)
        return None


def _report_run(summary: dict) -> int:
    if summary.get("test_double"):
        print(TEST_DOUBLE_BANNER)
    print(json.dumps({"digest": summary["digest"], "rulings": summary["rulings"], "cost": summary["cost"]}, indent=2))
    print("Retain digest.json separately from log.jsonl.")
    st = summary.get("stopped")
    if st:
        print(f"run stopped: {st['reason']}: {st['detail']}", file=sys.stderr)
        return 3 if st["reason"] == "run cap reached" else 4
    return 0


def _freeze_stamp(a) -> dict:
    """The freeze record in force at run time (if any), so scoring can tell runs made before a freeze."""
    p = Path(getattr(a, "freeze", None) or "evals/FREEZE.json")
    if not p.exists():
        return {"freeze_sha256": None}
    try:
        return {"freeze_sha256": json.loads(p.read_text(encoding="utf-8")).get("freeze_sha256")}
    except json.JSONDecodeError:
        return {"freeze_sha256": None}


def _load_kb(path):
    """The knowledge base with the reviewer-approved applicability records attached (gate rule 5)."""
    from .gate import load_applicability

    kb = KB.load(path)
    kb.applicability = load_applicability()
    return kb


def _cmd_run(a) -> int:
    from .journey import JOURNEY_ROLES, load_scenario, run_and_save

    backend = _backend_or_exit(a)
    if backend is None:
        return 2
    kb = _load_kb(a.kb)
    sc = load_scenario(a.scenario)
    names = {n for s in sc.steps for n in s.addressees}
    roles = [r for r in JOURNEY_ROLES if r.name in names]
    models = {r.name: backend for r in roles}
    import hashlib

    extra = {**_freeze_stamp(a), "scenario_file_sha256": hashlib.sha256(Path(a.scenario).read_bytes()).hexdigest()}
    summary = run_and_save(kb, sc, models, a.out, real=not a.test_double, code_version=_code_version(),
                           roles=roles, extra=extra)
    return _report_run(summary)


PROBE_REQUEST = ("Return the JSON object for a connectivity test: decision ESCALATE, rule_applied none, rationale probe, "
                 "citations [], applicability [], gap null, revision_reason null.")


def _cmd_probe(a) -> int:
    """Tiny calls that confirm which request settings the model accepts, before any scored run."""
    import hashlib
    from datetime import datetime, timezone

    from .audit import canonical
    from .llm import ModelCallFailed, parse_json_response
    from .roles import ASSESSMENT_SCHEMA

    backend = _backend_or_exit(a)
    if backend is None:
        return 2
    s = backend.settings
    variants = [("as configured", dict(s.thinking) if s.thinking else None, s.structured_output)]
    if s.thinking and "display" in s.thinking:
        variants.append(("thinking without display", {k: v for k, v in s.thinking.items() if k != "display"},
                         s.structured_output))
    if s.structured_output:
        variants.append(("prompt-instructed JSON", variants[-1][1], False))
    attempts, winner = [], None
    for name, thinking, structured in variants:
        backend.settings.thinking, backend.settings.structured_output = thinking, structured
        try:
            # the schema every role call sends, so a schema the API cannot compile fails here and not in the smoke session
            r = backend.complete("Reply with the JSON object only.", PROBE_REQUEST, schema=ASSESSMENT_SCHEMA)
        except ModelCallFailed as e:
            attempts.append({"variant": name, "result": "rejected by the API", "status": e.status_code,
                             "error": str(e)[:300]})
            continue
        ok = r.rejection is None
        if ok:
            try:
                ok = "decision" in parse_json_response(r.text)
            except ValueError:
                ok = False
        attempts.append({"variant": name, "result": "accepted" if ok else "answer rejected", "call": r.call_record()})
        if ok:
            winner = (name, thinking, structured)
            break
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(a.runs_root) / "_probe" / stamp
    out.mkdir(parents=True, exist_ok=True)
    cost = {"test_double": backend.is_test_double, **backend.ledger.summary()}
    (out / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    record = {"model_id": backend.model_id, "attempts": attempts, "accepted": winner[0] if winner else None,
              "schema": "ASSESSMENT_SCHEMA (the one every role call sends)",
              "schema_sha256": hashlib.sha256(canonical(ASSESSMENT_SCHEMA)).hexdigest(),
              "test_double": backend.is_test_double, "cost": cost, "code_version": _code_version()}
    (out / "probe.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if backend.is_test_double:
        print(TEST_DOUBLE_BANNER)
    for x in attempts:
        print(f"{x['variant']}: {x['result']}")
    print(f"cost USD {cost['cost_usd']:.6f}; record {out / 'probe.json'}")
    if not winner:
        print("no variant was accepted; do not start scored runs", file=sys.stderr)
        return 1
    if winner[0] != "as configured":
        msg = f"accepted settings differ from the config: {winner[0]}"
        if a.apply and not backend.is_test_double:
            cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
            cfg["thinking"], cfg["structured_output"] = winner[1], winner[2]
            cfg.setdefault("notes", []).append(f"changed by probe-model {stamp}: {winner[0]} (record {out.name})")
            Path(a.config).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            msg += f"; {a.config} updated"
        else:
            msg += "; rerun with --apply to record it in the config"
        print(msg)
    return 0


def _cmd_cost(a) -> int:
    from .llm import ModelSettings, cap_statement, prior_spend_usd

    s = ModelSettings.load(a.config)
    spent = prior_spend_usd(a.runs_root)
    print(f"spent so far by saved runs under {a.runs_root} (test doubles excluded; unfinished runs counted at their bound): "
          f"USD {spent:.4f}; total cap USD {s.total_cap_usd}; per-run cap USD {s.run_cap_usd}")
    if not a.short:
        print(cap_statement(s))
    return 0


def _cmd_probe_status(a) -> int:
    """Exit 0 only if a real (not test-double) probe on record accepted a request configuration using the assessment
    schema in force now (an older probe with a tiny stand-in schema does not show the API accepts the real one)."""
    import hashlib

    from .audit import canonical
    from .roles import ASSESSMENT_SCHEMA

    current = hashlib.sha256(canonical(ASSESSMENT_SCHEMA)).hexdigest()
    for p in sorted((Path(a.runs_root) / "_probe").rglob("probe.json")) if (Path(a.runs_root) / "_probe").exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if d.get("accepted") and not d.get("test_double") and d.get("schema_sha256") == current:
            print(f"probe accepted: {d['accepted']} ({p})")
            return 0
    print("no accepted probe on record for the current assessment schema; the probe will run")
    return 1


def _cmd_preflight(a) -> int:
    """Say what this Python can and cannot do before a session starts, with the install line for anything missing."""
    import platform
    import shutil

    missing = []
    print(f"python {platform.python_version()} at {sys.executable}")
    if sys.version_info < (3, 10):
        missing.append("Python 3.10 or newer")
    for mod, need in (("numpy", True), ("anthropic", not a.test_double)):
        try:
            m = __import__(mod)
            print(f"{mod} {getattr(m, '__version__', '?')}")
        except ImportError:
            print(f"{mod}: NOT INSTALLED" + ("" if need else " (not needed for a rehearsal)"))
            if need:
                missing.append(mod)
    print("git: " + ("found" if shutil.which("git") else "not found (runs would record the code version as 'unversioned')"))
    if missing:
        print("missing: " + ", ".join(missing) + '. From the repository root: python -m pip install -e ".[model]"', file=sys.stderr)
        return 2
    return 0


def _default_config() -> str:
    local = Path("config") / "run_config.json"
    return str(local if local.exists() else Path(__file__).resolve().parents[2] / "config" / "run_config.json")


def _cmd_add_step(a) -> int:
    from .journey import JOURNEY_ROLES, add_step, load_scenario, run_and_save, scenario_to_json

    try:
        sc = add_step(load_scenario(a.scenario), a.text, a.to)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    backend = _backend_or_exit(a)
    if backend is None:
        return 2
    out = Path(a.out)
    if (out / "log.jsonl").exists():
        print(f"error: {out / 'log.jsonl'} exists; a run directory is never overwritten", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "scenario_with_added_step.json").write_text(json.dumps(scenario_to_json(sc), indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
    names = {n for s in sc.steps for n in s.addressees}
    roles = [r for r in JOURNEY_ROLES if r.name in names]
    added = sc.steps[-1]
    import hashlib

    extra = {**_freeze_stamp(a), "judge_added_step": {
        "step_id": added.step_id, "to": added.addressees, "text_sha256": hashlib.sha256(added.text.encode("utf-8")).hexdigest(),
        "base_scenario": Path(a.scenario).name,
        "note": "a step written by the person running this command; the run is a protocol example, not a scored result"}}
    summary = run_and_save(_load_kb(a.kb), sc, {r.name: backend for r in roles}, out, real=not a.test_double,
                           code_version=_code_version(), roles=roles, extra=extra)
    print(f"trace: {out / 'trace.html'}")
    return _report_run(summary)


def _cmd_run_cases(a) -> int:
    from .cases import run_cases
    from .eval import EvalError, labels_sha256, load_labels

    labels = load_labels(a.labels)
    freeze = json.loads(Path(a.freeze).read_text(encoding="utf-8")) if Path(a.freeze).exists() else None
    if a.split == "heldout":
        from .cases import check_freeze_for_heldout

        try:
            from .eval import code_tree_sha256

            kb0 = _load_kb(a.kb)
            check_freeze_for_heldout(freeze, labels_sha256(a.labels), kb0.version,
                                     run={"code_tree_sha256": code_tree_sha256(), "code_version": _code_version(),
                                          "applicability_sha256": (kb0.applicability or {}).get("sha256")})
        except EvalError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    backend = _backend_or_exit(a)
    if backend is None:
        return 2
    s = run_cases(_load_kb(a.kb), labels, a.split, backend, a.out, real=not a.test_double,
                  labels_sha=labels_sha256(a.labels), freeze=freeze, code_version=_code_version(), case_ids=a.case)
    if s.get("test_double"):
        print(TEST_DOUBLE_BANNER)
    print(json.dumps({"split": s["split"], "cases": len(s["case_ids"]), "cost": s["cost"], "digest": s["digest"]}, indent=2))
    st = s.get("stopped")
    if st:
        print(f"run stopped: {st['reason']}: {st['detail']}", file=sys.stderr)
        return 3 if st["reason"] == "run cap reached" else 4
    return 0


def _cmd_approve_applicability(a) -> int:
    from .gate import APPLICABILITY_FILE, approve_applicability

    try:
        approve_applicability(a.file or APPLICABILITY_FILE, a.doc, a.reviewer)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"approved by {a.reviewer}: {', '.join(a.doc)}")
    return 0


def _cmd_freeze(a) -> int:
    from .eval import EvalError
    from .llm import ModelSettings
    from .score import make_freeze

    out = Path(a.out)
    if out.exists():
        print(f"error: {out} exists; a freeze is made once. Record a correction as a new file and keep the first "
              "held-out result", file=sys.stderr)
        return 2
    cfg = {k: v for k, v in ModelSettings.load(a.config).to_config().items()
           if k in ("model_id", "max_tokens", "effort", "thinking", "structured_output")}
    try:
        fr = make_freeze(a.labels, _load_kb(a.kb), a.scenario or [], cfg, a.reviewer, a.exposed or [],
                         a.attest_independent, _code_version())
    except EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fr, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"freeze {fr['freeze_sha256']} written to {out}; held-out cases {len(fr['heldout_case_ids'])}; "
          f"exposed and left out {len(fr['exposed_case_ids'])}; approved applicability records: {', '.join(fr['applicability_approved']) or 'none'}")
    return 0


def _cmd_score(a) -> int:
    from .score import ScoreRefused, score_runs, write_results

    try:
        res = score_runs(KB.load(a.kb), a.labels, a.runs, a.split, freeze_path=a.freeze, scenarios_dir=a.scenarios,
                         allow_test_double=a.allow_test_double, code_version=_code_version(),
                         allow_code_drift=a.allow_code_drift)
    except ScoreRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    write_results(res, a.out)
    if res["stamp"]["banner"]:
        print(res["stamp"]["banner"])
    oa = res["rows"]["outcome_agreement"]
    print(f"outcome agreement: full system {oa['system']['agree']} of {oa['system']['n']}, "
          f"baseline {oa['baseline']['agree']} of {oa['baseline']['n']}; runs counted {len(res['stamp']['run_ids'])}, "
          f"not counted {len(res['excluded_runs'])}")
    print(f"wrote {Path(a.out) / 'results.json'} and results.md")
    return 0


def _cmd_fetch(a) -> int:
    from .fetch import OK_STATUSES, ManifestError, fetch_sources, load_manifest

    try:
        m = load_manifest(a.manifest)
    except (ManifestError, OSError, json.JSONDecodeError) as e:
        print(f"error: manifest {a.manifest}: {e}", file=sys.stderr)
        return 2
    res = fetch_sources(m, a.root, only=a.only, check_only=a.check_only, keep_going=a.keep_going)
    for r in res:
        print(f"{r.doc_id:6} {r.status:17} {r.file}: {r.message}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps([r.__dict__ for r in res], indent=2), encoding="utf-8")
    ok = sum(r.status in OK_STATUSES for r in res)
    print(f"{ok} of {len(res)} files present with matching sha256")
    if any(r.status == "hash_mismatch" for r in res):
        print("stopped on a hash mismatch; nothing mismatched was written", file=sys.stderr)
        return 3
    if ok < len(res):
        print("some files need the browser step shown above (or a recorded hash); then rerun", file=sys.stderr)
        return 2
    return 0


def _cmd_prepare(a) -> int:
    from .prepare import PASSING, audit_report, check_corpus, rebuild, rebuild_all

    if a.rebuild:
        print(rebuild(a.root, a.rebuild))
        return 0
    if a.rebuild_all:
        skipped: list = []
        for p in rebuild_all(a.root, skipped=skipped):
            print("wrote", p)
        for doc_id, reason in skipped:
            print(f"skipped {doc_id}: {reason}")
    res = check_corpus(a.root, audit=not a.no_audit)
    for r in res:
        print(f"{r.doc_id:5} {r.status:11} {r.path}: {r.detail}")
    n_rep = sum(r.status == "REPRODUCED" for r in res)
    n_aud = sum(r.status == "AUDITED" for r in res)
    n_bad = sum(r.status not in PASSING for r in res)
    if not res:
        print("error: no corpus files were found under the given root; nothing was checked")
        return 1
    print(f"{n_rep} of {len(res)} corpus files rebuild byte for byte from their raw file by a recorded transform; "
          f"{n_aud} pass the verbatim audit only (every content line found in the hash-verified raw text, no byte-exact transform); "
          f"{n_bad} fail")
    if a.json_out:
        text = json.dumps(audit_report(res), indent=1, ensure_ascii=False) + chr(10)
        Path(a.json_out).write_text(text, encoding="utf-8", newline=chr(10))
    return 0 if n_bad == 0 else 1


def _cmd_report_fill(a) -> int:
    from .report import ReportError, collect_facts, estimate_pages, fill_template, lint_numerals

    tpl = Path(a.template).read_text(encoding="utf-8")
    facts = collect_facts(runs_root=a.runs, kb_dir=a.kb, offline_facts=a.offline_facts, results_dir=a.results, provenance=a.provenance,
                           cases_file=a.cases, dossier_evidence=a.dossier, retrieval_results=a.retrieval)
    try:
        out = fill_template(tpl, facts)
    except ReportError as e:
        raise SystemExit(f"error: {e}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(out, encoding="utf-8", newline=chr(10))
    pending = out.count("[pending-live")
    print(f"wrote {a.out}: {out.count('[measured')} measured, {out.count('[offline')} offline, {pending} pending-live values")
    print(f"estimated length {estimate_pages(out):.1f} pages at 500 words per page (the rendered page count in the official template decides)")
    bare = lint_numerals(tpl)
    if bare:
        print("untagged numerals in the template prose (each must be an identifier or become a {{fact:...}}):", ", ".join(bare))
    return 0


def _cmd_export_public(a) -> int:
    from .export import ExportBlocked, build_export

    try:
        rep = build_export(a.src, a.out, commit=a.commit)
    except ExportBlocked as e:
        print(f"export blocked: {e}", file=sys.stderr)
        return 2
    print(f"exported {rep['files']} files to {rep['out']}" + (f" (commit {rep['commit']}, no remote)" if rep.get("commit") else ""))
    for rel, q in sorted((rep.get("quotations") or {}).items()):
        print(f"quotes link-only text: {rel}: {q['runs']} run(s), longest {q['longest_words']} words")
    for rel, n in sorted((rep.get("placeholders") or {}).items()):
        print(f"unfilled [WALTER: ...] field: {rel}: {n}")
    return 0


def _cmd_label_cards(a) -> int:
    from .eval import EvalError, load_labels
    from .labelreview import render_cards

    try:
        labels = load_labels(a.labels)
    except EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    corrections = json.loads(Path(a.corrections).read_text(encoding="utf-8")) if a.corrections else None
    Path(a.out).write_text(render_cards(labels, KB.load(a.kb), corrections, Path(a.corrections).name if a.corrections else None),
                           encoding="utf-8", newline=chr(10))
    print(f"wrote {a.out}: {len(labels)} cards; open it in a browser (local use only, it contains link-only text)")
    return 0


def _cmd_approve_labels(a) -> int:
    from .eval import EvalError
    from .labelreview import approve_labels

    corrections = json.loads(Path(a.corrections).read_text(encoding="utf-8")) if a.corrections else None
    try:
        rows = approve_labels(a.labels, a.out, a.reviewer, approve="all" if a.approve == "all" else a.approve.split(","),
                              corrections=corrections)
    except EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    from collections import Counter

    print(f"wrote {a.out}: {dict(Counter(r['status'] for r in rows))}; next: gapcourt freeze --labels {a.out} ... --attest-independent")
    return 0


def _add_backend_args(p) -> None:
    p.add_argument("--config", default=_default_config())
    p.add_argument("--runs-root", default="runs", help="saved runs; their cost.json files count toward the total cap")
    p.add_argument("--test-double", action="store_true",
                   help="offline rehearsal with the labelled test double; no network, not a result")
    p.add_argument("--freeze", default="evals/FREEZE.json")


def _cmd_eval(a) -> int:
    from .eval import load_labels, score_retrieval

    from .eval import freeze_problem, labels_sha256

    kb = KB.load(a.kb)
    labels = load_labels(a.labels)
    freeze = json.loads(Path(a.freeze).read_text(encoding="utf-8")) if Path(a.freeze).exists() else None
    why = freeze_problem(freeze, labels_sha256(a.labels), kb.version)
    split = a.split
    if why and split == "heldout":
        print(f"error: held-out retrieval results are shown only after the freeze: {why}", file=sys.stderr)
        return 2
    if why and split is None:
        split = "dev"
        print("note: held-out cases withheld until the labels are frozen; showing the dev split only")
    unapproved = sum(1 for r in labels if r["status"] == "DRAFT_UNAPPROVED")
    if unapproved:
        print(f"note: {unapproved} labels are DRAFT_UNAPPROVED; output below tests the machinery and is not a result")
    for mode in ("bm25", "hybrid"):
        res = score_retrieval(kb, labels, a.collections.split(","), mode=mode, k=a.k, split=split)
        cats = {k: f"{v['hit']} of {v['n']}" for k, v in res.items() if isinstance(v, dict) and "hit" in v}
        print(mode, cats)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gapcourt", description=f"Gap Court {__version__} (work in progress)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-kb")
    b.add_argument("--root", action="append", required=True)
    b.add_argument("--public-root", action="append")
    b.add_argument("--out", required=True)
    b.add_argument("--public-out", help="also write the release export (link-only text and vectors redacted)")
    b.add_argument("--max-chars", type=int, default=1200)
    b.add_argument("--dim", type=int, default=1024, help="vector size of the hashing embedder")
    b.add_argument("--embedder", choices=("hashing", "ollama"), default="hashing",
                   help="hashing = local lexical stand-in; ollama = nomic-embed-text through the adapted ITU reference embed_texts")
    b.add_argument("--ollama-url", default="http://localhost:11434")
    b.add_argument("--ollama-model", default="nomic-embed-text")
    b.add_argument("--chroma-dir", help="also store the chunks in ChromaDB collections here (ITU reference store, adapted)")
    b.set_defaults(fn=_cmd_build)

    dc = sub.add_parser("dossier-check", help="verify every quotation and hash of a dossier spec and render the dossier")
    dc.add_argument("--spec", required=True)
    dc.add_argument("--kb", required=True)
    dc.add_argument("--audit", default="corpus/PROVENANCE_AUDIT.json")
    dc.add_argument("--repo-root", default=".")
    dc.add_argument("--out-md")
    dc.add_argument("--out-json")
    dc.add_argument("--strict", action="store_true", help="exit 1 if a quotation fails or a flagged finding is not listed in expected_flagged")
    dc.set_defaults(fn=_cmd_dossier_check)

    x = sub.add_parser("expand-query", help="show which concepts of the English-to-Arabic retrieval map a question triggers")
    x.add_argument("--query", required=True)
    x.set_defaults(fn=_cmd_expand_query)

    s = sub.add_parser("search")
    s.add_argument("--kb", required=True)
    s.add_argument("--collections", required=True)
    s.add_argument("--query", required=True)
    s.add_argument("--mode", default="hybrid", choices=["hybrid", "bm25", "vector"])
    s.add_argument("--k", type=int, default=5)
    s.set_defaults(fn=_cmd_search)

    c = sub.add_parser("coverage")
    c.add_argument("--kb", required=True)
    c.set_defaults(fn=_cmd_coverage)

    ps = sub.add_parser("probe-status", help="exit 0 if a real probe on record accepted a request configuration")
    ps.add_argument("--runs-root", default="runs")
    ps.set_defaults(fn=_cmd_probe_status)

    pf = sub.add_parser("preflight", help="check this Python has what a live or rehearsal session needs")
    pf.add_argument("--test-double", action="store_true", help="rehearsal: the anthropic package is not needed")
    pf.set_defaults(fn=_cmd_preflight)

    v = sub.add_parser("verify-log")
    v.add_argument("--log", required=True)
    v.add_argument("--digest")
    v.set_defaults(fn=_cmd_verify)

    r = sub.add_parser("run-scenario")
    r.add_argument("--kb", required=True)
    r.add_argument("--scenario", required=True)
    r.add_argument("--out", required=True)
    _add_backend_args(r)
    r.set_defaults(fn=_cmd_run)

    pm = sub.add_parser("probe-model", help="confirm accepted request settings with tiny calls")
    _add_backend_args(pm)
    pm.add_argument("--apply", action="store_true", help="record a changed setting in the config file")
    pm.set_defaults(fn=_cmd_probe)

    ad = sub.add_parser("add-step", help="run a scenario plus one step you write (judge-added case)")
    ad.add_argument("--kb", required=True)
    ad.add_argument("--scenario", required=True)
    ad.add_argument("--to", action="append", required=True, help="addressee role (repeatable): service, adviser, custodian, oversight")
    ad.add_argument("--text", required=True)
    ad.add_argument("--out", required=True)
    _add_backend_args(ad)
    ad.set_defaults(fn=_cmd_add_step)

    rc = sub.add_parser("run-cases", help="run labelled cases through the full system and the baseline")
    rc.add_argument("--kb", required=True)
    rc.add_argument("--labels", required=True)
    rc.add_argument("--split", required=True, choices=["dev", "heldout"])
    rc.add_argument("--out", required=True)
    rc.add_argument("--case", action="append", help="case_id to run (repeatable); default all in the split")
    _add_backend_args(rc)
    rc.set_defaults(fn=_cmd_run_cases)

    fz = sub.add_parser("freeze", help="freeze held-out labels, expected rulings and configuration before scored runs")
    fz.add_argument("--labels", required=True)
    fz.add_argument("--kb", required=True)
    fz.add_argument("--scenario", action="append")
    fz.add_argument("--config", default=_default_config())
    fz.add_argument("--reviewer", required=True)
    fz.add_argument("--exposed", action="append", help="held-out case_id whose model answer was seen before the freeze")
    fz.add_argument("--attest-independent", action="store_true",
                    help="labels and expected rulings were reviewed from source passages without seeing model answers")
    fz.add_argument("--out", default="evals/FREEZE.json")
    fz.set_defaults(fn=_cmd_freeze)

    sc = sub.add_parser("score", help="recompute every reported count from saved runs (no model calls)")
    sc.add_argument("--kb", required=True)
    sc.add_argument("--labels", required=True)
    sc.add_argument("--runs", default="runs")
    sc.add_argument("--split", required=True, choices=["dev", "heldout"])
    sc.add_argument("--out", required=True)
    sc.add_argument("--freeze", default="evals/FREEZE.json")
    sc.add_argument("--scenarios", default="scenarios")
    sc.add_argument("--allow-test-double", action="store_true", help="count test-double runs (output is stamped TEST DOUBLE)")
    sc.add_argument("--allow-code-drift", action="store_true",
                    help="count held-out runs made with other code than the frozen code, labelled in the results")
    sc.set_defaults(fn=_cmd_score)

    kf = sub.add_parser("kb-fetch", help="download link-only raw files from the manifest and check sha256")
    kf.add_argument("--manifest", default="corpus/SOURCES.json")
    kf.add_argument("--root", default=".")
    kf.add_argument("--only", action="append", help="doc_id to fetch (repeatable)")
    kf.add_argument("--check-only", action="store_true", help="check local files; no network")
    kf.add_argument("--json-out", help="also write the results as JSON")
    kf.add_argument("--keep-going", action="store_true", help="report every mismatch instead of stopping (diagnosis)")
    kf.set_defaults(fn=_cmd_fetch)

    pr = sub.add_parser("prepare", help="check that corpus files rebuild exactly from raw files")
    pr.add_argument("--root", default=".")
    pr.add_argument("--rebuild", metavar="DOC_ID", help="rewrite one corpus file from its raw file")
    pr.add_argument("--rebuild-all", action="store_true", help="write every corpus file that has a recorded transform (fresh clone, after kb-fetch)")
    pr.add_argument("--no-audit", action="store_true", help="skip the verbatim audit: files without a byte-exact transform fail as NO_RECIPE")
    pr.add_argument("--json-out", help="write the text-free audit report as JSON")
    pr.set_defaults(fn=_cmd_prepare)

    rf = sub.add_parser("report-fill", help="fill the report template from run output; every number is tagged measured, offline or pending-live")
    rf.add_argument("--template", default="report/TEMPLATE_V3_FILLABLE.md")
    rf.add_argument("--out", default="docs/REPORT_DRAFT_5PAGE.md")
    rf.add_argument("--runs", default="runs")
    rf.add_argument("--kb", default="build/kb")
    rf.add_argument("--results", default="results/heldout")
    rf.add_argument("--offline-facts", default="report/OFFLINE_FACTS.json")
    rf.add_argument("--provenance", default="corpus/PROVENANCE_AUDIT.json")
    rf.add_argument("--cases", default="evals/pilot_cases_DRAFT.jsonl")
    rf.add_argument("--dossier", default="dossiers/DOSSIER_S01.evidence.json")
    rf.add_argument("--retrieval", default="results/bilingual_retrieval_after.json")
    rf.set_defaults(fn=_cmd_report_fill)

    lc = sub.add_parser("label-cards", help="one printable card per label case with the source passage inline (no model answers)")
    lc.add_argument("--labels", default="evals/pilot_cases_DRAFT.jsonl")
    lc.add_argument("--kb", default="build/kb")
    lc.add_argument("--out", default="label_cards/cards.html")
    lc.add_argument("--corrections", help="show the proposed corrections in this file on the cards (shown, not applied)")
    lc.set_defaults(fn=_cmd_label_cards)

    aa = sub.add_parser("approve-applicability", help="mark the named instruments' applicability records approved by the reviewer")
    aa.add_argument("--reviewer", required=True)
    aa.add_argument("--doc", action="append", required=True, help="doc id, for example U06; repeat for each")
    aa.add_argument("--file", help="default: corpus/APPLICABILITY.json")
    aa.set_defaults(fn=_cmd_approve_applicability)

    al = sub.add_parser("approve-labels", help="write the approved label file: named cases APPROVED, corrected cases CORRECTED")
    al.add_argument("--labels", default="evals/pilot_cases_DRAFT.jsonl")
    al.add_argument("--out", default="evals/labels_v1.jsonl")
    al.add_argument("--reviewer", required=True)
    al.add_argument("--approve", default="all", help="'all' or a comma-separated list of case ids")
    al.add_argument("--corrections", help="JSON file: {case_id: {field: new value}}")
    al.set_defaults(fn=_cmd_approve_labels)

    ex = sub.add_parser("export-public", help="clean whitelist export with a fresh single-commit history and no remote")
    ex.add_argument("--src", default=".")
    ex.add_argument("--out", required=True, help="a new folder; the export never overwrites")
    ex.add_argument("--commit", action="store_true")
    ex.set_defaults(fn=_cmd_export_public)

    co = sub.add_parser("cost", help="USD spent by saved runs")
    co.add_argument("--config", default=_default_config())
    co.add_argument("--runs-root", default="runs")
    co.add_argument("--short", action="store_true", help="only the USD line (used inside sessions)")
    co.set_defaults(fn=_cmd_cost)

    e = sub.add_parser("eval-retrieval")
    e.add_argument("--kb", required=True)
    e.add_argument("--labels", required=True)
    e.add_argument("--collections", required=True)
    e.add_argument("--k", type=int, default=5)
    e.add_argument("--split", choices=["dev", "heldout"])
    e.add_argument("--freeze", default="evals/FREEZE.json", help="held-out results are shown only with a valid freeze")
    e.set_defaults(fn=_cmd_eval)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
