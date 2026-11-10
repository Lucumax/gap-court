# Gap Court

Gap Court is a decision aid for one simulated public-service journey. It bases its answers on public policy and legal texts. Built for the AI for Good Lab Hackathon (United Arab Emirates). Team: Juncture Policy. Team leader: Walter Guevara.

Independent entry. No affiliation with or endorsement by ITU. The sources are public UAE legal and policy texts, with links. No UAE authority endorsement is implied.

Status: prototype, submitted entry. Live session on 2026-11-10 with frozen labels: on the held-out cases, each run once, the full system agreed with the approved label in 7 of 8 cases and the retrieval-plus-abstain baseline in 2 of 8 (`results/heldout/results.md`). This is a small pilot with author-made cases and one reviewer; it does not measure legal correctness. In the scenario runs every S-01, S-02 and S-04 step was ruled ESCALATE, so the DENY, ALLOW and scope rulings the labels expect were not reached. All saved runs together cost USD 0.0566 (`gapcourt cost`). Every report number comes from recorded work: `gapcourt report-fill` tags it measured (from a model run) or offline (from checks without model calls). Known limitations are listed in `NOT_DONE.md`.

## What it does

- Ingests public instruments into a versioned knowledge base with stable chunk ids and article and page locators (`src/gapcourt/corpus.py`, `chunking.py`, `index.py`).
- Retrieves with BM25, a local vector arm and reciprocal-rank fusion, scoped by collection (`bm25.py`, `embed.py`, `retrieve.py`). BM25 alone and retrieval-plus-abstain are the baselines.
- Runs four model-backed roles (service agent, policy and legal adviser, two institutional participants) and a coordinator that routes and reconciles by fixed rules, with separate retrieval scopes and message partitions, on scenarios released one step at a time (`roles.py`, `scenario.py`, `journey.py`). Instruction-like text inside a quoted passage is refused as support by a small pattern list; this is a narrow defense, not a guarantee.
- Calls the model through the Anthropic Messages API with the official `anthropic` SDK (`llm.py`). The model id, output budget (16,000 tokens), effort (medium), thinking display and structured output come from `config/run_config.json`; no sampling parameter is sent. A truncated, refused, empty or unparseable answer (after one retry) is logged as a visible `MODEL_REJECTED` record and the step goes to a person; it is never counted as an abstention (`test_max_tokens_stop_is_visible_rejection_not_abstain` and neighbours in `tests/test_model_connector.py`).
- Prices every call from its usage and, before each call, reserves an upper bound for that call (input tokens cannot exceed the UTF-8 byte count of the request, plus 2,000 for what the provider adds; output cannot exceed `max_tokens`, which the API enforces) and sends it only if spent + reserved + in flight + that bound fits under the per-run cap (USD 2) and the total cap across saved runs (USD 18). A failed attempt, or a crash or Ctrl+C while a request is out, stays reserved at its bound and is written to a journal that the next run's total reads. Under the stated assumptions no single call can take spending past a cap; what is assumed and not enforced (an unconfirmed price table, no other spender on the key) is printed exactly by `gapcourt cost` (`tests/test_cost_caps.py`). A cap stops the run and the partial log still verifies.
- Keeps a deterministic gate: a kept statement needs a quotation that a mechanical check finds in the cited passage ("quotation verified"). That check does not show that the passage supports the conclusion, applies to the actor, or reads the law correctly; quotation presence is not a legal interpretation. An analyst reviews that (`verify.py`, `gate.py`).
- Types every non-answer (retrieval miss, coverage gap, ambiguity or conflict, implementation gap, unresolved) and calls a gap a candidate policy gap only after a named reviewer promotes it (`gaps.py`).
- Writes a hash-chained audit log with source traces, stop reasons, token use, cost, effort and the provider summary of model reasoning (labelled as such, not the raw reasoning), checked against a separately retained terminal digest (`audit.py`). The last record of every run binds a hash of the whole `summary.json` (cost, code version, model settings, freeze, everything except the digest field itself) and of the `cases.jsonl` sidecar into that digest, so editing either file after the run is detected by `verify-log`, `score` and the report (`tests/test_summary_binding.py`). The freeze and each run also carry a hash of the package source; a held-out run made with other code than the frozen code is refused before any call, or left out of the score unless `--allow-code-drift` labels it. The check reports internal consistency only. It does not show authorship or truth.

## Knowledge base acquisition

The UAE instruments are link-only under their publishers' terms, so their full text is not in this repository. Short verification quotations (the longest is 31 words, each with its locator) appear in the case labels, the dossiers, the generator scripts and the gate records of the saved live runs; the export refuses any passage longer than 40 words of link-only text and lists every file that quotes. `corpus/SOURCES.json` is an acquisition manifest: for each raw file it gives the official address, the expected sha256 and size, and where to save it. `gapcourt kb-fetch` downloads the entries marked `automatic` with a plain client, checks each hash, stops and names the file on a mismatch, and never overwrites a local file. Entries marked `manual` are hosts that refused a scripted client, or texts not yet obtained; for them the command prints "open <url> in a browser, save to <path>, then rerun".

A dated check on 2026-11-01 from a fresh folder fetched 14 of 16 automatic files with matching hashes, including all 8 UAE raw files; the result is recorded per entry (`last_checked`). Two global sources (G1, G6) did not serve the recorded file to a plain client and are marked manual, as is U01EN (the official English text, saved by hand in a browser), and two UAE texts not yet obtained (U04, U10). Hosts can refuse later even where they worked before, so complete automatic retrieval is not promised.

`gapcourt prepare` accounts for every corpus file against its hash-verified raw file. Ten of 19 rebuild byte for byte by a recorded transform (U01 by a Python recipe; U06, U08, U11, U12, U13, U15, U16, G6 and G7 by an extractor plus a guarded structural patch in `corpus/PREP.json`). The other nine (G1, G2, G2A, G3, G4, G5, G8, F1, F2) are curated excerpts that join or flatten lines; they pass a verbatim audit in which every content line is found in the raw text under a named normalization, with no unmatched line and no registered edit. The audit is a statement about the words, not the bytes. `gapcourt prepare --rebuild-all` writes the reproducible files after `gapcourt kb-fetch`. Details, classification and limits: `docs/CORPUS_PROVENANCE.md`; text-free report: `corpus/PROVENANCE_AUDIT.json`.

## Commands

```
gapcourt build-kb --root corpus --root corpus_local --out build/kb
gapcourt kb-fetch [--check-only]                  # acquisition manifest, sha256 checks
gapcourt prepare [--rebuild-all] [--json-out F]    # corpus provenance: reproduced or audited, nothing unaccounted
gapcourt verify-log --log runs/<run>/log.jsonl --digest runs/<run>/digest.json
gapcourt probe-model                               # tiny calls: which request settings the model accepts
gapcourt run-scenario --kb build/kb --scenario scenarios/S-01.json --out runs/S-01/r1
gapcourt run-cases --kb build/kb --labels evals/labels_v1.jsonl --split dev --out runs/dev/<stamp>
gapcourt freeze --labels evals/labels_v1.jsonl --kb build/kb --scenario scenarios/S-01.json --scenario scenarios/S-02.json --reviewer "<name>" --attest-independent
gapcourt score --kb build/kb --labels evals/labels_v1.jsonl --runs runs --split heldout --out results/heldout
gapcourt add-step --kb build/kb --scenario scenarios/S-01.json --to adviser --text "<your step>" --out runs/judge/<name>
gapcourt cost                                      # USD spent by saved runs, and exactly what the cost caps hold to
gapcourt preflight [--test-double]                 # what this Python has (numpy, anthropic, git) and the install line if not
gapcourt probe-status                              # exit 0 if a real probe accepted a request configuration
gapcourt label-cards --out label_cards/cards.html  # one card per case, source passage inline, no model answers (local use)
gapcourt approve-labels --reviewer "<name>" [--approve all|PC01,PC02] [--corrections fixes.json]
gapcourt report-fill                               # fill the five-page source from run output; numbers tagged
gapcourt expand-query --query "<question>"         # which concepts of the English-to-Arabic retrieval map a question triggers (a retrieval aid, not a translation)
gapcourt dossier-check --spec dossiers/DOSSIER_S01.spec.json --kb build/kb --out-md docs/DOSSIER_S01.md --out-json dossiers/DOSSIER_S01.evidence.json --strict
python scripts/build_report_docx.py --template <official template .docx> --out report/REPORT_v2.docx   # render the report draft into the official Word template (new file)
python scripts/bilingual_probe.py --kb build/kb     # offline retrieval check for the Arabic-only instruments, with and without the bilingual path
gapcourt export-public --out ../gapcourt_public_export --commit   # whitelist copy, secret and path scan, no git remote
gapcourt build-kb ... --embedder ollama --chroma-dir build/chroma  # the ITU reference embedding and store path
```

Commands that call the model read `ANTHROPIC_API_KEY` from the environment of the shell that runs them; the key is never written to a file or a log. Each of them also takes `--test-double`, an offline rehearsal with a labelled test double: no network, every output is stamped TEST DOUBLE, and `gapcourt score` does not count it. `scripts/walter_live_runs.ps1` runs the planned sessions (probe, smoke, dev, held-out, repeats) and prints the cost after each command.

Scoring reads saved run folders only. A held-out score is refused unless a freeze record matches the label file, the held-out labels are approved, and the reviewer attested that labels and expected rulings were reviewed from source passages without seeing model answers (`test_score_refuses_unfrozen_labels_for_heldout`). The smoke session uses only the unscored scenario S-03 and designated dev cases.

## What it does not claim

No accuracy, compliance, certification, legal advice or conformity with ITU-T Y.3172. The Y.3172 mapping is by analogy. The embedder in the default build is a local hashed lexical model (`hashing-lexical-v1`), not a neural model. The ITU reference ingestion code is vendored unmodified and its embedding and store functions are adapted and tested against the originals with doubles (`NOTICE`, `src/gapcourt/itu_adapter.py`); the run against a live Ollama server with `nomic-embed-text`, which is what `gapcourt build-kb --embedder ollama --chroma-dir <dir>` does, has not been done. Unit tests use labelled test doubles (`ScriptedModel`, `TestDoubleAnthropicClient`) and never a live model; both are refused for reported runs. A legal status, applicability date or amendment number stays unverified until its primary instrument, article and effective date are recorded.

## Layout

```
src/gapcourt/     package
tests/            unit tests (synthetic fixtures are labelled SYNTHETIC)
config/           model and cost settings for runs
corpus/           redistributable documents (public tree) and the acquisition manifest SOURCES.json
corpus_local/     link-only documents and raw downloads (git-ignored)
evals/            pilot case labels (drafts are marked DRAFT_UNAPPROVED)
scenarios/        simulated journey scenarios (SYNTHETIC, author-made)
dossiers/         evidence skeletons for three investigations, and the source-checked dossier for S-01 step 2 (spec, evidence ledger; rendered at docs/DOSSIER_S01.md)
report/           fillable five-page source (TEMPLATE_V3_FILLABLE.md) and the offline facts it cites
scripts/          build helpers, the live-run script, derive_prep.py, fresh_clone_check.py
docs/             corpus format, corpus provenance, what the code decides versus the model (DETERMINISTIC_LAYER), Arabic and English handling (BILINGUAL_HANDLING), the report draft
third_party/      vendored ITU reference ingestion files (unmodified) with sha256 list
kb_release/       the public knowledge base export (link-only text redacted), in the public export only
```

## Quickstart

No API key is needed for any step below.

```
python -m pip install -e ".[dev,prepare]"
python -m pytest -q                                   # offline suite, no network
gapcourt kb-fetch --keep-going                        # needs network; entries marked manual print a browser step
gapcourt prepare --rebuild-all
gapcourt build-kb --root corpus --root corpus_local --out build/kb
gapcourt run-scenario --kb build/kb --scenario scenarios/S-03.json --out runs_rehearsal/S-03 --test-double
gapcourt verify-log --log runs_rehearsal/S-03/log.jsonl --digest runs_rehearsal/S-03/digest.json
```

In a fresh clone two steps exit with a non-zero code, by design: `gapcourt kb-fetch --keep-going` exits 2 because the manual files (G1, G6, U01EN, U04, U08 transcription, U10) cannot be fetched by a script and it prints the browser step for each, and `gapcourt prepare --rebuild-all` exits 1 because it then lacks those raw files (it reports which ones fail). Neither stops what follows: `build-kb` exits 0 and builds a smaller knowledge base from the files that were fetched, and the last two commands exit 0. The rehearsal run uses a labelled test double: no model is called, the output is stamped TEST DOUBLE and is not a result. Open `runs_rehearsal/S-03/trace.html` in a browser to see the trace page, then change one character in `log.jsonl` and run `verify-log` again: it names the first broken record. The knowledge base this builds from a fresh clone is a subset of the one the report cites (the report names the version each number refers to; see Knowledge base acquisition). Saved live runs are under `runs/` and verify the same way, for example `gapcourt verify-log --log runs/S-01/r1/log.jsonl --digest runs/S-01/r1/digest.json`. The held-out run's log and trace page are not in the public copy (one verified quotation in them is longer than the 40 words the export allows for link-only text; see `runs/heldout/20261110T092416Z/WITHHELD.md`); its summary, cases and digest are. The five-page report is a separate file; its source text, with every number tagged, is `docs/REPORT_DRAFT_5PAGE.md`.

## Run the tests

```
python -m pip install -e ".[dev]"
python -m pytest -q
```

To rebuild the corpus files from the downloaded raw texts (`gapcourt kb-fetch`, then `gapcourt prepare --rebuild-all`) also install the preparation packages: `python -m pip install -e ".[dev,prepare]"`.

No test needs a key or the network.

## Disclosure

LLM-assisted development I specified, directed and reviewed. The audit log, retrieval, agent, scoring and report code are original to this repository. The ingestion embedding and store step adapts the ITU reference repository (CrashingGuru/ITUAIReadiness, MIT licence, commit 84010da); the vendored files are in `third_party/itu_reference/` and every change is listed in `NOTICE`. Role outputs come from Claude Haiku 5.5 (`claude-haiku-5-5`) through the Anthropic API. The knowledge base, agents, scenarios, evaluation and report are built for this hackathon.

## Licence

MIT. See `LICENSE`. Third-party documents keep their own terms; see `corpus/` front matter (`terms_note`, `republish`).
