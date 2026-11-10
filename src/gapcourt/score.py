"""`gapcourt score`: every reported count, recomputed from saved run folders only (no model calls).

Rows, each as K of N with the baseline beside it where one exists:
1 governing-passage retrieval in top 5 (BM25, vector, fused) by case type; 2 outcome agreement with labels,
full system vs retrieval-plus-abstain; 3 false gap findings and false answers; 4 scenario steps matching the
expected ruling; 5 revisions after contradicting steps and stale rulings; 6 quotations kept and dropped by
the gate; 7 model rejections by reason, tokens and USD; 8 KB release (questions reopened).

Freeze rules: a held-out score needs a freeze record made by `gapcourt freeze` whose
label hash matches the label file, whose held-out labels are APPROVED and whose reviewer attested that
labels and expected rulings were reviewed from source passages without seeing model answers. Only runs
made under that freeze, with the frozen configuration, count; the first such held-out run is the one
reported; cases the freeze lists as exposed are left out. Scenario rows say whether the expected rulings
were frozen before the run, and whether an earlier run of the same scenario predates the freeze.

A run whose log does not verify against its digest is excluded, as is a test-double run unless
allow_test_double is set; then every output carries the TEST DOUBLE banner.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .audit import AuditLog, canonical, check_summary_binding, verify_records
from .cases import CASE_COLLECTIONS, cases_from_records
from .eval import (EvalError, applicability_problem, code_drift_problem, code_tree_sha256 as _code_tree_sha256, freeze_is_intact, freeze_problem,
                   labels_sha256, load_labels, score_retrieval)
from .index import KB

TEST_DOUBLE_BANNER = "TEST DOUBLE RUNS INCLUDED: no model was called; these counts test the machinery and are not results"
MODES = ("bm25", "vector", "hybrid")
FREEZE_COMPARE_KEYS = ("model_id", "max_tokens", "effort", "thinking", "structured_output")


class ScoreRefused(RuntimeError):
    pass


def _sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _expected_token(v) -> str | None:
    if not v:
        return None
    return str(v).split(":")[0].split("(")[0].strip().split(" ")[0]


# ---- freeze -----------------------------------------------------------------

def make_freeze(labels_path, kb: KB, scenario_paths: list, model_config: dict, reviewer: str,
                exposed_case_ids: list[str], attest_independent: bool, code_version: str, top_k: int = 5,
                abstain_threshold: float = 0.35, code_tree_sha256: str | None = None) -> dict:
    labels = load_labels(labels_path)
    blocked = [r["case_id"] for r in labels if r.get("blocked_on")]
    held = [r for r in labels if r["split"] == "heldout" and not r.get("blocked_on")]
    if not held:
        raise EvalError("no held-out cases in the label file")
    unapproved = [r["case_id"] for r in held if r["status"] not in ("APPROVED", "CORRECTED")]
    if unapproved:
        raise EvalError(f"held-out labels must be APPROVED before a freeze: {', '.join(unapproved)}")
    if not attest_independent:
        raise EvalError("the reviewer must attest that labels and expected rulings were reviewed from source passages "
                        "without seeing model answers (--attest-independent)")
    if not reviewer:
        raise EvalError("a named reviewer is required")
    appl = getattr(kb, "applicability", None)
    if not appl or not appl.get("approved") or appl.get("sha256") in (None, "", "missing"):
        raise EvalError("no applicability record is approved in corpus/APPLICABILITY.json (or the file is missing or not loaded), so "
                        "every ALLOW or DENY would become ESCALATE; approve the reviewed records first "
                        "(gapcourt approve-applicability --reviewer NAME --doc ID ...)")
    scen = {}
    for p in scenario_paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        scen[d["scenario_id"]] = {"file": Path(p).name, "sha256": _sha_file(p),
                                  "expected": {s["step_id"]: s.get("hidden", {}).get("expected_candidate")
                                               for s in d["steps"]}}
    body = {"labels_file": Path(labels_path).name, "labels_sha256": labels_sha256(labels_path),
            "heldout_case_ids": [r["case_id"] for r in held],
            "dev_case_ids": [r["case_id"] for r in labels if r["split"] == "dev" and not r.get("blocked_on")],
            "blocked_case_ids": sorted(blocked),
            "kb_version": kb.version, "top_k": top_k, "fusion": {"rrf_k": 60, "w_bm25": 1.0, "w_vec": 1.0},
            "abstain_threshold": abstain_threshold, "model_config": model_config, "code_version": code_version,
            "code_tree_sha256": code_tree_sha256 or _code_tree_sha256(),
            "applicability_sha256": (getattr(kb, "applicability", None) or {}).get("sha256"),
            "applicability_approved": sorted((getattr(kb, "applicability", None) or {}).get("approved", [])),
            "scenarios": scen, "exposed_case_ids": sorted(set(exposed_case_ids)), "reviewer": reviewer,
            "reviewed_without_model_answers": True, "single_reviewer_pilot": True,
            "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    return {**body, "freeze_sha256": hashlib.sha256(canonical(body)).hexdigest()}


# ---- run discovery ----------------------------------------------------------

def _load_run(d: Path, root: Path):
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    digest = json.loads((d / "digest.json").read_text(encoding="utf-8"))
    recs = AuditLog.load_records(d / "log.jsonl")
    v = verify_records(recs, expected=digest)
    rid = f"{d.relative_to(root).as_posix()}@{digest.get('last_hash', '')[:12]}"
    return s, recs, v, rid


def _event(recs: list[dict], name: str) -> dict:
    for r in recs:
        if r.get("kind") == "event" and r.get("event") == name:
            return r.get("payload") or {}
    return {}


def _log_is_test_double(recs: list[dict]) -> bool:
    return any(str(r.get("model_id") or "").startswith(("TEST-DOUBLE", "scripted")) for r in recs if r.get("kind") == "step")


def _model_settings(s: dict) -> list[dict]:
    out = []
    for cfg in (s.get("models") or {}).values():
        st = cfg.get("settings") or cfg
        out.append(st)
    return out


def _config_matches_freeze(s: dict, freeze: dict) -> bool:
    want = freeze.get("model_config") or {}
    for st in _model_settings(s):
        for k in FREEZE_COMPARE_KEYS:
            if k in want and st.get(k) != want[k]:
                return False
    return True


# ---- rows -------------------------------------------------------------------

def _agreement(case_rows: list[dict], labels: dict, key: str) -> dict:
    agree = n = 0
    by: dict = {}
    for c in case_rows:
        lab = labels.get(c["case_id"])
        if lab is None:
            continue
        ok = c[key] == lab["expected_outcome"]
        n += 1
        agree += ok
        b = by.setdefault(lab["category"], {"agree": 0, "n": 0})
        b["n"] += 1
        b["agree"] += ok
    return {"agree": agree, "n": n, "by_category": by}


def _false_findings(case_rows: list[dict], labels: dict, key: str) -> dict:
    fg = {"k": 0, "n": 0}
    fa = {"k": 0, "n": 0}
    for c in case_rows:
        lab = labels.get(c["case_id"])
        if lab is None:
            continue
        if lab["expected_outcome"] in ("ANSWER", "SCOPE_FINDING"):
            fg["n"] += 1
            fg["k"] += c[key] == "GAP"
        if lab["expected_outcome"] in ("GAP", "ABSTAIN"):
            fa["n"] += 1
            fa["k"] += c[key] == "ANSWER"
    return {"false_gap": fg, "false_answer": fa}


def _paired(case_rows: list[dict], labels: dict, key: str) -> dict:
    """One side of the paired table. Decisive means outcome ANSWER; unsupported means the approved label expects another outcome."""
    c = Counter(n=0, correct=0, decisive=0, unsupported_decisive=0, escalated=0, abstained=0, model_failures=0)
    for r in case_rows:
        lab = labels.get(r["case_id"])
        if lab is None:
            continue
        out = r[key]
        c["n"] += 1
        c["correct"] += out == lab["expected_outcome"]
        c["decisive"] += out == "ANSWER"
        c["unsupported_decisive"] += out == "ANSWER" and lab["expected_outcome"] != "ANSWER"
        c["escalated"] += out == "ESCALATE"
        c["abstained"] += out in ("ABSTAIN", "GAP")
        c["model_failures"] += out == "MODEL_REJECTED"
    return dict(c)


def _scenario_rows(recs: list[dict], scen: dict) -> tuple[dict, dict]:
    rulings = {r["payload"]["step_id"]: r["payload"]["decision"] for r in recs
               if r.get("kind") == "event" and r.get("event") == "ruling"}
    revised = {r["payload"]["step_id"] for r in recs if r.get("kind") == "event" and r.get("event") == "revision"}
    scope = {r["input"].get("step_id") for r in recs if r.get("kind") == "step" and (r.get("gate") or {}).get("scope_findings")}
    steps, blocked, matched, n, contra, rev = [], [], 0, 0, 0, 0
    for s in scen["steps"]:
        h = s.get("hidden") or {}
        tok = _expected_token(h.get("expected_candidate"))
        if tok and h.get("blocked_on"):
            blocked.append({"step_id": s["step_id"], "blocked_on": h["blocked_on"]})
            tok = None
        if tok:
            obs = "SCOPE_FINDING" if (tok == "SCOPE_FINDING" and s["step_id"] in scope) else rulings.get(s["step_id"])
            ok = obs == tok
            n += 1
            matched += ok
            steps.append({"step_id": s["step_id"], "expected": tok, "observed": obs, "match": ok})
        if h.get("contradicts_prior"):
            contra += 1
            rev += s["step_id"] in revised
    return ({"matched": matched, "n": n, "steps": steps, "blocked": blocked},
            {"revised": rev, "contradicting": contra, "stale": contra - rev})


def _gate_and_cost(recs: list[dict], acc: dict) -> None:
    for r in recs:
        if r.get("kind") != "step":
            continue
        g = r.get("gate") or {}
        acc["kept"] += len(g.get("kept") or [])
        for d in g.get("dropped") or []:
            acc["dropped"] += 1
            acc["dropped_by_reason"][d.get("reason", "unknown")] += 1
        if r.get("decision") == "MODEL_REJECTED":
            acc["rej"][(r.get("rejection") or {}).get("reason", "unknown")] += 1
        for c in r.get("model_calls") or []:
            acc["calls"] += 1
            u = c.get("usage") or {}
            acc["in"] += int(u.get("input_tokens") or 0)
            acc["out"] += int(u.get("output_tokens") or 0)
            acc["usd"] += float(c.get("cost_usd") or 0.0)


def score_runs(kb: KB, labels_path, runs_root, split: str, freeze_path=None, scenarios_dir=None,
               allow_test_double: bool = False, code_version: str = "unversioned", allow_code_drift: bool = False) -> dict:
    labels_list = load_labels(labels_path)
    lsha = labels_sha256(labels_path)
    freeze = None
    if freeze_path and Path(freeze_path).exists():
        freeze = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
        if not freeze_is_intact(freeze):
            raise ScoreRefused(f"freeze record {freeze_path} does not match its own freeze_sha256")
    blocked_rows = [r for r in labels_list if r["split"] == split and r.get("blocked_on")]
    split_rows = [r for r in labels_list if r["split"] == split and not r.get("blocked_on")]
    if split == "heldout":
        if freeze is None:
            raise ScoreRefused("held-out labels are not frozen: no freeze record (run gapcourt freeze first)")
        why = freeze_problem(freeze, lsha, kb.version)
        if why:
            raise ScoreRefused(f"held-out labels are not frozen: {why}")
        if any(r["status"] not in ("APPROVED", "CORRECTED") for r in split_rows):
            raise ScoreRefused("held-out labels are not frozen: some held-out labels are not APPROVED")
    exposed = sorted(set((freeze or {}).get("exposed_case_ids") or []) & {r["case_id"] for r in split_rows}) \
        if split == "heldout" else []
    scored_rows = [r for r in split_rows if r["case_id"] not in exposed]
    labels = {r["case_id"]: r for r in scored_rows}
    statuses = Counter(r["status"] for r in split_rows)
    label_status = ("APPROVED (frozen)" if split == "heldout" else
                    ("DRAFT_UNAPPROVED labels present: machinery check, not a result"
                     if statuses.get("DRAFT_UNAPPROVED") else "APPROVED"))

    root = Path(runs_root)
    excluded, case_runs, scen_runs, judge_runs = [], [], [], []
    for sp in sorted(root.rglob("summary.json")) if root.exists() else []:
        d = sp.parent
        if not (d / "log.jsonl").exists() or not (d / "digest.json").exists():
            continue
        s, recs, v, rid = _load_run(d, root)
        if not v.ok:
            excluded.append({"run": rid, "reason": "log failed verification against its digest: " + v.errors[0]})
            continue
        problem = check_summary_binding(recs, s, json.loads((d / "digest.json").read_text(encoding="utf-8")), d)
        if problem:
            excluded.append({"run": rid, "reason": problem})
            continue
        s = dict(s, test_double=bool(s.get("test_double")) or _log_is_test_double(recs))
        if s.get("test_double") and not allow_test_double:
            excluded.append({"run": rid, "reason": "test double run (no model was called)"})
            continue
        if freeze is not None and s.get("freeze_sha256") == freeze.get("freeze_sha256"):
            why = applicability_problem(freeze, s)
            if why:
                excluded.append({"run": rid, "reason": why})
                continue
        kind = s.get("kind", "scenario")
        if kind == "cases":
            if s.get("split") != split:
                continue
            start = _event(recs, "cases_start")
            if split == "heldout":
                if (s.get("freeze_sha256") != freeze.get("freeze_sha256") or s.get("labels_sha256") != lsha
                        or start.get("freeze_sha256") != freeze.get("freeze_sha256") or start.get("labels_sha256") != lsha):
                    excluded.append({"run": rid, "reason": "made before the freeze or under another freeze "
                                                           "(the summary and the verified log must both name this freeze)"})
                    continue
                if start.get("kb_version") != freeze.get("kb_version"):
                    excluded.append({"run": rid, "reason": "made on a different knowledge base than the frozen one"})
                    continue
                want_model = (freeze.get("model_config") or {}).get("model_id")
                logged = {r.get("model_id") for r in recs if r.get("kind") == "step"}
                if want_model and logged and logged != {want_model} and not (
                        s.get("test_double") and {str(m).replace("TEST-DOUBLE(", "").rstrip(")") for m in logged} == {want_model}):
                    excluded.append({"run": rid, "reason": "the model id in the verified log differs from the frozen model"})
                    continue
                if not _config_matches_freeze(s, freeze):
                    excluded.append({"run": rid, "reason": "model configuration differs from the freeze record"})
                    continue
                drift = code_drift_problem(freeze, s)
                if drift and not allow_code_drift:
                    excluded.append({"run": rid, "reason": "made with other code than the frozen code: " + drift
                                                           + " (score --allow-code-drift counts it with a label)"})
                    continue
            case_runs.append((s, recs, rid, d))
        elif s.get("judge_added_step"):
            judge_runs.append({"run_id": rid, "step": s["judge_added_step"]})
        else:
            scen_runs.append((s, recs, rid))

    stamp_runs, models, efforts, code_versions, test_double = [], set(), set(), set(), False
    code_drift: list[dict] = []

    def note_run(s, rid):
        nonlocal test_double
        stamp_runs.append(rid)
        for cfg in (s.get("models") or {}).values():
            models.add(cfg.get("model_id") or (cfg.get("settings") or {}).get("model_id") or "unknown")
            eff = (cfg.get("settings") or {}).get("effort")
            if eff:
                efforts.add(eff)
        code_versions.add(s.get("code_version", "unversioned"))
        test_double = test_double or bool(s.get("test_double"))
        if freeze is not None:
            drift = code_drift_problem(freeze, s)
            if drift:
                code_drift.append({"run": rid, "reason": drift})

    acc = {"kept": 0, "dropped": 0, "dropped_by_reason": Counter(), "rej": Counter(), "calls": 0, "in": 0, "out": 0,
           "usd": 0.0}
    rows: dict = {}
    # rows 2, 3: one case run per split (held-out: the first frozen run; dev: see below)
    case_runs.sort(key=lambda x: (x[0].get("started_at", ""), x[2]))
    case_rows, chosen_ids = [], []
    admitted = []
    for s, recs, rid, d in case_runs:
        derived = cases_from_records(recs, labels)
        side = d / "cases.jsonl"
        if side.exists():
            side_rows = [json.loads(x) for x in side.read_text(encoding="utf-8").splitlines() if x.strip()]

            def key(rs):
                return sorted((c["case_id"], c["system_outcome"], c["baseline_outcome"]) for c in rs)

            if key(side_rows) != key(derived):
                excluded.append({"run": rid, "reason": "the cases.jsonl sidecar disagrees with the verified log; "
                                                       "outcomes are read from the log, so this run is not counted"})
                continue
        admitted.append((s, rid, derived, recs))
    if split == "heldout":
        # the first outcome of each case is kept; a later run counts only for cases no earlier run produced
        seen: dict[str, dict] = {}
        for s, rid, derived, recs in admitted:
            used = False
            for c in derived:
                if c["case_id"] in labels and c["case_id"] not in seen:
                    seen[c["case_id"]] = c
                    used = True
            if used:
                chosen_ids.append((s, rid, recs))
            else:
                excluded.append({"run": rid, "reason": "later held-out run; the first frozen outcome of each case is kept"})
        case_rows = [seen[k] for k in labels if k in seen]
    elif admitted:
        # dev: the run covering the most dev cases, latest first (a smoke subset never replaces a full dev run)
        best = max(admitted, key=lambda x: (len(x[0].get("case_ids") or []), x[0].get("started_at", ""), x[1]))
        for a_ in admitted:
            if a_ is not best:
                excluded.append({"run": a_[1], "reason": "another dev run; the latest run covering the most dev cases is the one reported"})
        chosen_ids.append((best[0], best[1], best[3]))
        case_rows = [c for c in best[2] if c["case_id"] in labels]
    for s, rid, recs in chosen_ids:
        note_run(s, rid)
        _gate_and_cost(recs, acc)
    have = {c["case_id"] for c in case_rows}
    missing = [k for k in labels if k not in have]
    rows["outcome_agreement"] = {"system": _agreement(case_rows, labels, "system_outcome"),
                                 "baseline": _agreement(case_rows, labels, "baseline_outcome"),
                                 "excluded_exposed": exposed, "run_id": chosen_ids[0][1] if chosen_ids else None,
                                 "missing_cases": missing, "blocked_cases": [r["case_id"] for r in blocked_rows],
                                 "baseline_name": "retrieval plus abstain threshold (no roles, no gate)"}
    rows["paired"] = {"case_ids": [c["case_id"] for c in case_rows if c["case_id"] in labels],
                      "system": _paired(case_rows, labels, "system_outcome"),
                      "baseline": _paired(case_rows, labels, "baseline_outcome"),
                      "note": "same cases, same knowledge base, same retrieval settings (top 5, kb_uae); the baseline calls no model, "
                              "so it has no model failures and never escalates"}
    rows["false_findings"] = {"system": _false_findings(case_rows, labels, "system_outcome"),
                              "baseline": _false_findings(case_rows, labels, "baseline_outcome")}

    # row 1: retrieval, from the KB and labels only
    ret = {}
    for mode in MODES:
        r = score_retrieval(kb, scored_rows, [c for c in CASE_COLLECTIONS if c in kb.collections], mode=mode, k=5)
        cats = {k: v for k, v in r.items() if isinstance(v, dict) and "hit" in v}
        ret[mode] = {"all": {"hit": sum(v["hit"] for v in cats.values()), "n": sum(v["n"] for v in cats.values())}, **cats}
    rows["retrieval_top5"] = ret

    # rows 4, 5: scenario runs
    scen_files = {}
    if scenarios_dir and Path(scenarios_dir).exists():
        for p in sorted(Path(scenarios_dir).glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if "scenario_id" in d and "steps" in d:
                scen_files[d["scenario_id"]] = (d, _sha_file(p))
    pre_freeze = Counter()
    for s, _recs, _rid in scen_runs:
        if freeze is None or s.get("freeze_sha256") != freeze.get("freeze_sha256"):
            pre_freeze[s.get("scenario_id")] += 1
    rows["scenario_steps"], rows["revisions"] = [], []
    for s, recs, rid in sorted(scen_runs, key=lambda x: (x[0].get("started_at", ""), x[2])):
        sid = s.get("scenario_id")
        if sid not in scen_files:
            excluded.append({"run": rid, "reason": f"scenario file for {sid} not found under {scenarios_dir}"})
            continue
        scen, fsha = scen_files[sid]
        frozen = bool(freeze and s.get("freeze_sha256") == freeze.get("freeze_sha256")
                      and (freeze.get("scenarios") or {}).get(sid, {}).get("sha256") == fsha)
        st, rv = _scenario_rows(recs, scen)
        note_run(s, rid)
        _gate_and_cost(recs, acc)
        common = {"run_id": rid, "scenario_id": sid, "frozen": frozen, "exposed": bool(freeze) and pre_freeze[sid] > 0,
                  "scenario_file_changed": bool(s.get("scenario_file_sha256")) and s.get("scenario_file_sha256") != fsha,
                  "development_demonstration": bool(scen.get("development_demonstration")),
                  "stopped": s.get("stopped")}
        rows["scenario_steps"].append({**common, **st})
        rows["revisions"].append({**common, **rv})
    rows["judge_added_runs"] = judge_runs
    rows["quotations"] = {"kept": acc["kept"], "dropped": acc["dropped"], "dropped_by_reason": dict(acc["dropped_by_reason"]),
                          "note": "kept means the quotation was found in the cited passage; it is not a legal interpretation"}
    rows["model_rejections"] = {"by_reason": dict(acc["rej"]), "calls": acc["calls"], "input_tokens": acc["in"],
                                "output_tokens": acc["out"], "usd": round(acc["usd"], 6)}
    receipts = sorted(root.rglob("release_receipt.json")) if root.exists() else []
    if receipts:
        counts = Counter()
        for p in receipts:
            counts.update(json.loads(p.read_text(encoding="utf-8")).get("counts", {}))
        rows["kb_release"] = {"receipts": [p.relative_to(root).as_posix() for p in receipts], "counts": dict(counts)}
    else:
        rows["kb_release"] = {"note": "no KB-release receipt saved under the runs folder"}

    stamp = {"split": split, "labels_file": Path(labels_path).name, "labels_sha256": lsha, "label_status": label_status,
             "freeze_sha256": (freeze or {}).get("freeze_sha256"), "kb_version": kb.version,
             "models": sorted(models), "efforts": sorted(efforts), "code_version_scoring": code_version,
             "run_code_versions": sorted(code_versions), "run_ids": stamp_runs, "test_double": test_double,
             "banner": TEST_DOUBLE_BANNER if test_double else None,
             "code_differs_from_freeze": code_drift,
             "scored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"stamp": stamp, "rows": rows, "excluded_runs": excluded}


def _kn(k, n) -> str:
    return f"{k} of {n}"


def write_results(res: dict, out_dir) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    st, rows = res["stamp"], res["rows"]
    L = ["# Gap Court results", ""]
    if st["banner"]:
        L += [f"**{st['banner']}**", ""]
    L += [f"- split: {st['split']}; labels: {st['labels_file']} sha256 `{st['labels_sha256']}` ({st['label_status']})",
          f"- freeze record: `{st['freeze_sha256']}`" if st["freeze_sha256"] else "- freeze record: none",
          f"- KB version: `{st['kb_version']}`; models: {', '.join(st['models']) or 'none'}; effort: {', '.join(st['efforts']) or 'n/a'}",
          f"- code: scoring {st['code_version_scoring']}; runs {', '.join(st['run_code_versions']) or 'none'}",
          f"- runs: {', '.join(st['run_ids']) or 'none'}"]
    if st.get("code_differs_from_freeze"):
        L += ["- **Code differs from the frozen code for: "
              + "; ".join(f"{x['run']} ({x['reason']})" for x in st["code_differs_from_freeze"])
              + ". These runs are counted with this label; they are not strictly under the freeze.**"]
    L += [""]
    L += ["## 1. Governing passage in top 5", "", "| case type | BM25 | vector | fused |", "|---|---|---|---|"]
    cats = sorted({c for m in rows["retrieval_top5"].values() for c in m})
    for c in cats:
        cells = [_kn(rows["retrieval_top5"][m].get(c, {}).get("hit", 0), rows["retrieval_top5"][m].get(c, {}).get("n", 0))
                 for m in MODES]
        L.append(f"| {c} | " + " | ".join(cells) + " |")
    oa = rows["outcome_agreement"]
    L += ["", "## 2. Outcome agreement with labels", "", f"Run: {oa['run_id'] or 'none'}. Baseline: {oa['baseline_name']}.", "",
          "| case type | full system | baseline |", "|---|---|---|"]
    for c in sorted(set(oa["system"]["by_category"]) | set(oa["baseline"]["by_category"])):
        sy, bl = oa["system"]["by_category"].get(c, {"agree": 0, "n": 0}), oa["baseline"]["by_category"].get(c, {"agree": 0, "n": 0})
        L.append(f"| {c} | {_kn(sy['agree'], sy['n'])} | {_kn(bl['agree'], bl['n'])} |")
    L.append(f"| all | {_kn(oa['system']['agree'], oa['system']['n'])} | {_kn(oa['baseline']['agree'], oa['baseline']['n'])} |")
    if oa["excluded_exposed"]:
        L.append(f"\nLeft out as exposed before the freeze: {', '.join(oa['excluded_exposed'])}.")
    if oa.get("missing_cases"):
        L.append(f"\nNot run, so not in the counts above: {', '.join(oa['missing_cases'])}.")
    if oa.get("blocked_cases"):
        L.append(f"\nBlocked on a missing source text, so not run or scored: {', '.join(oa['blocked_cases'])}.")
    pr = rows["paired"]
    sy, bl = pr["system"], pr["baseline"]
    L += ["", "## 2b. Paired comparison on identical cases", "", pr["note"] + ".", "",
          "| count of cases | full system | retrieval plus abstain |", "|---|---|---|"]
    for label, k in (("cases", "n"), ("outcome agreement (same label as the approved one)", "correct"), ("decisive recommendations", "decisive"),
                     ("unsupported decisive recommendations", "unsupported_decisive"), ("escalations", "escalated"),
                     ("abstentions or gap findings", "abstained"), ("model failures", "model_failures")):
        L.append(f"| {label} | {sy[k]} | {bl[k]} |")
    L += ["", "Unsupported decisive: answered where the approved label expects another outcome. Counts show pilot agreement on "
                "a few cases; they do not establish legal correctness."]
    ff = rows["false_findings"]
    L += ["", "## 3. False gap findings and false answers", "", "| | full system | baseline |", "|---|---|---|",
          f"| gap where the label has an answer | {_kn(**ff['system']['false_gap'])} | {_kn(**ff['baseline']['false_gap'])} |",
          f"| answer where the label has a gap or abstention | {_kn(**ff['system']['false_answer'])} | {_kn(**ff['baseline']['false_answer'])} |"]
    def kind(r):
        return r["scenario_id"] + (" (development demonstration, reported separately)" if r.get("development_demonstration") else "")

    L += ["", "## 4. Scenario steps matching the expected ruling", "", "| run | scenario | matched | expected rulings frozen | earlier run before freeze |",
          "|---|---|---|---|---|"]
    for r in rows["scenario_steps"]:
        L.append(f"| {r['run_id']} | {kind(r)} | {_kn(r['matched'], r['n'])} | {'yes' if r['frozen'] else 'no'} | {'yes' if r['exposed'] else 'no'} |")
    L += ["", "## 5. Revisions after contradicting steps", "", "| run | revised | stale rulings left standing |", "|---|---|---|"]
    for r in rows["revisions"]:
        L.append(f"| {r['run_id']} ({kind(r)}) | {_kn(r['revised'], r['contradicting'])} | {_kn(r['stale'], r['contradicting'])} |")
    q = rows["quotations"]
    L += ["", "## 6. Quotations", "", f"Kept {q['kept']}, dropped {q['dropped']} (by reason: {json.dumps(q['dropped_by_reason'])}). {q['note']}."]
    mr = rows["model_rejections"]
    L += ["", "## 7. Model rejections, tokens and cost", "",
          f"Rejections by reason: {json.dumps(mr['by_reason'])}; calls {mr['calls']}; input tokens {mr['input_tokens']}; "
          f"output tokens {mr['output_tokens']}; USD {mr['usd']:.4f}."]
    kr = rows["kb_release"]
    L += ["", "## 8. KB release", "", kr.get("note") or f"Receipts {', '.join(kr['receipts'])}: {json.dumps(kr['counts'])}."]
    if rows.get("judge_added_runs"):
        L += ["", "Judge-added step runs (protocol examples, not counted above): " + ", ".join(r["run_id"] for r in rows["judge_added_runs"])]
    if res["excluded_runs"]:
        L += ["", "## Runs not counted", ""] + [f"- {x['run']}: {x['reason']}" for x in res["excluded_runs"]]
    (out / "results.md").write_text("\n".join(L) + "\n", encoding="utf-8")
