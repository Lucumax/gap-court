# Review of the first live smoke session (2026-11-02, 01:45 UTC)

Status: a plumbing test on unapproved draft labels with one model (`claude-haiku-5-5`) and four role calls. It is not a result and not evidence of accuracy. Judgments are marked as judgments. Code under test: `fa2335b`. This review was written without any model call and without reading any key.

## What ran

| Run | Folder | Calls | Tokens in / out | Cost (USD) |
|---|---|---|---|---|
| Probe 1 (tiny schema, Nov 1) | `runs/_probe/20261101T151834Z` | 1 | 222 / 9 | 0.0000267 |
| Probe 2 (assessment schema, current) | `runs/_probe/20261102T014610Z` | 1 | 977 / 59 | 0.0001272 |
| Scenario S-03, once | `runs/smoke/S-03-20261102T014545Z` | 1 | 3492 / 400 | 0.0005492 |
| Dev cases PC01, PC03, PC11 | `runs/smoke/dev3-20261102T014545Z` | 3 | 8780 / 1605 | 0.0016805 |
| Total | | 6 | 13471 / 2073 | 0.0023836 (reported USD 0.0024) |

## 1. Verification

- `gapcourt verify-log` passes for both smoke runs: S-03 7 records, dev3 8 records, each matching its retained `digest.json`; `summary.json` (and the `cases.jsonl` sidecar for dev3) is bound into the digest.
- Cost, recomputed independently from the log (usage times the configured price of USD 0.10 per million input and 0.50 per million output tokens): every call matches its logged `cost_usd` (S-03 0.0005492; PC01 0.0007022; PC03 0.0003929; PC11 0.0005854; probe 2 0.0001272; probe 1 0.0000267).
- The ledger (`runs/_ledger`) agrees: each `spent` line equals the run's logged cost; the `prior_spent_usd` chain in the `cost.json` files is consistent (0.000154 before S-03, 0.000703 before dev3); `gapcourt cost` prints USD 0.0024. Each call reserved about USD 0.009 before it was sent and released it to the actual cost; the cap never came near (per run USD 2, total USD 18).
- The price table is the one in the Claude API reference (cached 2026-10-06) for this model (USD 0.10 / 0.50 per million up to 100K input tokens). That is a second reading of one source, not the live pricing page. This stays open in `NOT_DONE.md` (costs): the Console usage page should be compared with USD 0.0024 once.
- All four role calls ended `end_turn`, with no retry, no rejection (`model_rejections` is empty in both summaries), no truncation. Structured output with the assessment schema was accepted and parsed on the first attempt every time. The first role calls made with real prompts also confirm that effort `medium`, adaptive thinking with `display: summarized` and the 16,000-token output budget are accepted together.
- The report draft's 18 measured values were checked against the runs (a separate recount from `log.jsonl`: 4 calls, 12,272 in, 2,005 out, end_turn 4, kept 3, dropped 0, cost 0.00223). They match. One report defect was found and fixed (section 4).

## 2. Outcomes against the draft labels (dev split, unapproved)

| Case | Category | Label expects | System outcome (decision) | Baseline (retrieval plus abstain) | Match |
|---|---|---|---|---|---|
| PC01 | answerable | ANSWER, U06 Article 15 | ANSWER (DENY), kept U06 Article 15 and U12 Article 23 | ABSTAIN (support 0.2705 under threshold 0.35) | yes; baseline wrong |
| PC03 | answerable | ANSWER, U13 Article 4 | ANSWER (ALLOW), kept U13 Article 4 | ANSWER | yes |
| PC11 | missing_document | ABSTAIN | SCOPE_FINDING (decision GAP) | ABSTAIN | no |
| S-03 (scenario, expected "GAP corpus coverage gap without the benefit instrument") | trap | GAP | ruling GAP, gap type `corpus_coverage_gap`, no citation | n/a | yes |

n is 3 and the labels are drafts, so this shows only that the pipeline can produce each outcome type, not a rate. The cited chunks for PC01 and PC03 are the expected document and locator, and the first quotation of each contains the label's basis quote.

## 3. Cite-or-refuse behaviour

- Statements kept: 3 (PC01 two, PC03 one), each with a quotation the gate found in the cited chunk and marked binding; dropped by the gate: 0. S-03 and PC11 kept nothing and refused with GAP, which is the intended behaviour for a question no retrieved passage answers.
- Judgment: the PC01 rationale also mentions U12 Article 17 ("treating disclosure ... as a confidentiality harm"), a passage that was retrieved but not cited. The rationale text is free text outside the gate. Nothing is wrong with the decision, but a reader could take that sentence as supported when it is not. The trace shows rationale and citations separately; the report must not quote a rationale as if it were gated.
- The quotation check does not test meaning: see the next section.

## 4. What failed or needs a decision

1. **Spurious scope findings (prompt issue, fixed in the prompt, unmeasured).** On S-03 and PC11 the model marked off-topic passages `applies: no`, and the gate turned each into an "instrument does not apply, per ..." scope finding: 2 on S-03, 4 on PC11. The quoted words were real, but they were definitions, aims and principles, not scope or exclusion clauses (for example "Entities should maximize the insights derived from data"). The prompt said only "for instruments whose scope you checked". The contract now says to use `no` only for the instrument's own scope, application or exclusion clause that excludes the actor or situation, and to leave merely off-topic passages out. This is a wording change; whether the model follows it is for the next dev session to show.
2. **PC11 outcome mapping.** `outcome_of` maps any kept scope finding to SCOPE_FINDING, so the spurious findings turned a GAP into a SCOPE_FINDING. With the prompt fix the mapping should stop firing on such cases; it was not changed, because a genuine scope case (held-out ambiguity case expecting SCOPE_FINDING) may also come with decision GAP or ESCALATE, and the label review should decide the mapping (NOT_DONE 12).
3. **GAP versus ABSTAIN (prompt and label design).** The case adviser instruction already said "ABSTAIN if the question lies outside the reviewed instruments", but the shared contract said "If no passage governs the question, answer GAP", and the model was never shown which instruments are reviewed. It answered GAP, which is what the contract told it to do. Fixed in the prompt: the contract defers to role instructions, and the case adviser now gets a line listing the reviewed instruments (document id and title) with the rule "GAP if they cover the subject but state no rule; ABSTAIN if none of them concerns the subject". Separate design question for the label reviewer, not changed here: scenario S-03 expects GAP (typed `corpus_coverage_gap`) for the same kind of missing-instrument question that the four `missing_document` cases label ABSTAIN. The two conventions should be reconciled before the freeze, with the held-out labels (PC12, PC14) left untouched until the reviewer decides.
4. **Report total (fixed test-first).** The draft said "Total spent across saved runs: USD 0.0022 over 4 calls" while `gapcourt cost` said USD 0.0024; the report left out the two probe calls. The total now includes them (USD 0.0024 over 6 calls).
5. **Two non-model defects found by the offline suite and the report linter (fixed).** The export test failed because the saved live runs carry verification quotations from link-only instruments in their gate records and trace pages (the longest, 31 words by the exporter's measure, is a verified quote of one source line, within the 40-word limit the exporter has always enforced). Deliberate decision: that is the intended content of a gate record, so the test now allows a source line of at most 40 words inside `runs/` and still fails on anything longer (new tests cover both directions). It also means NOT_DONE 21 (may short link-only quotations be published?) now covers the live runs too; the README figure moved from 22 to 31 words. The report linter reported "21, 21" because the identifier pattern took "M-25" from "OMB M-25-21" and left "21"; fixed.

## 5. Observations without a fix

- Adaptive thinking produced a visible summary only for PC01 (829 output tokens); the other three calls show "no summary", with 296 to 480 output tokens. "No summary" does not mean no reasoning: thinking is billed either way. The S-03 call used 400 output tokens for a two-sentence rationale.
- The S-03 retrieval query is the step text plus the role's fixed focus phrase ("which instrument governs this use of personal records and who decides"). The top five hits (glossary, register definitions, data privacy principles) are about personal records, none about benefits, which is consistent with the true coverage gap but means the focus phrase, not the step, steers retrieval. Judgment: acceptable for a trap scenario, worth a look for scored scenarios after the freeze plan is fixed.
- Cases PC01 and PC03 each retrieved the expected chunk (rank 4 and rank 1).

## 5a. Read against the official event page (facts supplied Nov 2)

- Accuracy: the criteria list scores "relevance and accuracy of agent responses during simulated error scenarios" while the FAQ says prototype accuracy is not a criterion. Plan on accuracy being scored. The report no longer says accuracy is not a criterion. On the three dev cases the full system agreed with the draft label twice; that is not an accuracy figure.
- Chain-of-thought traces: the auditability criterion names them. The model service returns no raw reasoning. In this session a provider summary came back on one of four calls, the other three had none. What the trace reliably holds is the role's rationale, rule applied, cited quotations with a mechanical check, and the retrieved passages with hashes. Trade-off for the reviewer: (a) say exactly that (current report wording), (b) additionally require a short structured step-by-step rationale field from the model, which is a model-written explanation and not its reasoning, would cost output tokens, and must not be labelled chain of thought. Nothing was added to the schema.

## 6. Fixes in this session (test first)

| Commit | Content |
|---|---|
| ae63a6c (red) / c47b597 (green) | Export test deliberately allows verification quotes in saved runs and still blocks passage-length copies; export commit identity; LICENSE line; `M-25-21` numeral; contract wording on `applies: no` and on GAP versus ABSTAIN; case prompt lists reviewed instruments |
| a420f6c (red) / 170e2af (green) | Report total spent includes probe calls |
| c6e6509 (red) / c7fd5e9 (green) | The exported commit now contains the saved live runs: the copied `.gitignore` lists `runs/`, so the first rebuild this session committed none of the 15 run files |
| cc8c515, 76d2964, 8aacbac | README and NOT_DONE updated; offline facts (358 passed, 1 skipped) and report draft refreshed |

## 7. Next live session

The runs and their cost are in the table under "What ran" above.

## 8. Dev run (2026-11-02, 10:22 UTC)

Status: the first run of all ten development cases, after the earlier prompt revisions. Labels are the unapproved drafts (`DRAFT_UNAPPROVED`), nothing is frozen, no held-out case was run. These are development numbers, to find faults, not results. Run folder `runs/dev/20261102T102235Z`, code `1c4c063`, model `claude-haiku-5-5`, effort medium, KB `kb-b08e7cfac2df`. This section was written without any model call and without reading any key.

### 8.1 Verification

- `gapcourt verify-log` passes: 22 records match the retained `digest.json` (last hash `c02e0935...`), and `summary.json` and `cases.jsonl` are bound into the digest.
- Cost recomputed from the log (usage times USD 0.10 / 0.50 per million tokens): all 10 calls match their logged `cost_usd` to the last digit; total 35,933 in and 7,749 out, USD 0.007468. The ledger file `20261102T052241-ddf9ca6a.jsonl` has 10 `begin` and 10 `spent` lines summing to USD 0.0074678, with no unreleased reservation. All four ledger files together are USD 0.0098247; adding the first probe (USD 0.0000267, no ledger file) gives USD 0.0098514, which `gapcourt cost` prints as USD 0.0099 over 16 calls (1 + 1 + 3 + 10 + the first probe). `cost.json` agrees (`prior_spent_usd` 0.002384, the smoke total of USD 0.0023836 rounded).
- 10 calls, 10 `end_turn`, no retry, no rejection (`model_rejections` is empty), no truncation. The cost is inside the estimate (about USD 0.006; USD 0.005 to 0.010 was the stated range); each call reserved about USD 0.009 and released it.
- Provider reasoning summary: present on 9 of 10 calls (PC03 returned the label only). In the smoke session it came on 1 of 4. Judgment: it is a model summary, not the raw reasoning, and the trace should keep saying so.
- Code at the time of the run was `1c4c063`. Afterwards only `scripts/repo_lint.py` and its test changed; nothing under `src/gapcourt`, so a re-run would send the same prompts.

### 8.2 Per case (labels are drafts)

| Case | Category | Label | System (decision) | Match | Baseline | Expected passage among the 5 retrieved | Kept / dropped quotations | USD | Out tokens |
|---|---|---|---|---|---|---|---|---|---|
| PC01 | answerable | ANSWER | ANSWER (DENY) | yes | ABSTAIN (0.271) | U06 Art 15 yes | 2 / 0 (U06 Art 15, U12 Art 17) | 0.000727 | 775 |
| PC03 | answerable | ANSWER | ANSWER (ALLOW) | yes | ANSWER (0.366) | U13 Art 4 yes | 1 / 0 | 0.000448 | 301 |
| PC05 | answerable | ANSWER | ANSWER (ALLOW) | yes | ABSTAIN (0.296) | U12 Art 23 yes | 2 / 0 (both U12 Art 23, same chunk) | 0.000819 | 833 |
| PC07 | paraphrase_trap | ANSWER | GAP | no | ABSTAIN (0.093) | neither U06 Art 15 nor U12 Art 17 | 0 / 0 | 0.000717 | 748 |
| PC09 | paraphrase_trap | ANSWER | GAP | no | ABSTAIN (0.177) | U16 Section 8 by locator; the chunk with 8.13 and 8.14 was not retrieved | 1 / 0 (U16 Section 6, non-binding) | 0.000704 | 762 |
| PC11 | missing_document | ABSTAIN | ABSTAIN | yes | ABSTAIN (0.159) | none expected | 0 / 0 | 0.000688 | 581 |
| PC13 | missing_document | ABSTAIN | GAP | no | ABSTAIN (0.161) | none expected | 0 / 0 | 0.000715 | 677 |
| PC15 | ambiguity | ESCALATE | ESCALATE | yes | ABSTAIN (0.178) | U12 Art 23 yes, U15 DE6 no | 1 / 0 (U12 Art 23) | 0.000998 | 1201 |
| PC17 | ambiguity | ESCALATE | GAP | no | ABSTAIN (0.248) | neither U13 Art 5 nor U06 Art 9 | 2 / 0 (U13 Art 4, U13 Art 1) | 0.000882 | 1090 |
| PC19 | candidate_gap | GAP | GAP | yes | ABSTAIN (0.094) | none expected | 0 / 0 | 0.000770 | 781 |

The system agrees with the draft label on 6 of 10; the retrieval-plus-threshold baseline on 3 of 10 (PC03, PC11, PC13). `gapcourt score` counts the same (section 8.4).

### 8.3 What the run shows

1. **The spurious `applies: no` findings are gone.** Scope findings: 0 in all ten cases (smoke: 4 on PC11, 2 on S-03). The prompt revision worked on this model for these questions; it is not measured beyond them.
2. **PC11 now ABSTAINs** (smoke: GAP, mapped to SCOPE_FINDING). Its rationale says the reviewed instruments do not concern federal social support, which is the intended reading of the case instruction, and it cites no passage. The line listing the reviewed instruments did what it was added for.
3. **PC13 still answers GAP, and I think the draft label is the problem, not the model.** The label says ABSTAIN because U02 (the federal Data Office decree-law) is missing. But the KB contains U01, the federal personal data law (Arabic print edition, 45 passages), and the "reviewed instruments" line lists it. The model reasoned that U01 "may address complaints" and that no passage of it was retrieved, so it answered GAP. That is a defensible reading of the instructions it was given. The deeper cause is that the only U01 text is Arabic with text-layer defects (NOT_DONE 10), so an English question rarely retrieves it. The same pattern shows in PC07, PC17 and PC19, whose rationales also say U01 may cover the point but nothing from it was retrieved. Decision for the label review: treat Arabic-only instruments as reviewed (then PC13 is a GAP case) or as not reviewed (then remove them from the line, and PC13 is ABSTAIN).
4. **PC07 and PC09 are retrieval misses, not reasoning failures.** The model answered GAP because the right passage was not among the five it saw (PC07: the question says "ministry" and "registry details", U06 Art 15 says "Dubai Data"; PC09: the chunk holding 8.13 and 8.14 of the access guide was not retrieved, another chunk of the same section was). Judgment: a gap statement is the honest outcome here, not a wrong answer, and it counts against the draft label. Separate point on PC09: its basis quote is from U16, a non-binding guide, and by rule R1 a non-binding passage cannot support ALLOW or DENY. Even with the right chunk the system could not reach ANSWER, so the label conflicts with the gate; the reviewer should move PC09 to a binding basis or expect something other than ANSWER.
5. **PC17 (label ESCALATE) answered GAP,** with two kept quotations from U13 (Art 4: the register is the only official source; Art 1: a definition). The expected passages (U13 Art 5, U06 Art 9) were not retrieved, and the model said the federal once-only policy is not among the reviewed instruments. Plausible either way; the label's own note says reach over a federal recipient is unresolved, which is what ESCALATE means. Reviewer decision.
6. **Cite-or-refuse.** Kept 9, dropped 0 in the dev run. Every kept quotation was found in its cited chunk and marked binding, except PC09's U16 passage (non-binding, attached to a GAP). No ALLOW or DENY was issued without a binding verified quotation (PC01, PC03, PC05). The six outcomes that are not ANSWER never stated a rule. Because nothing was dropped, the gate's drop path was not exercised live (tests cover it). Judgments on relevance, which the gate does not test: PC01's second quotation (U12 Art 17) concerns disclosure for another purpose being a confidentiality classification, a looser fit than the first; PC17's U13 Art 1 quotation is a definition that supports nothing in the ruling. The rationale text remains free text outside the gate.
7. **Before and after on the smoke cases.** PC01: ANSWER both times, the second citation changed (U12 Art 23 then, U12 Art 17 now). PC03: same outcome, 296 then 301 output tokens. PC11: GAP with 4 spurious scope findings, now ABSTAIN with none. Output varies between runs (one sample each).
8. **Retrieval query note (no fix made).** The query logged per case is `[case PC09] <question>`: the label id is part of the retrieval query. `gapcourt score` row 1 retrieves with the bare question, so its numbers differ from what the role saw (score row, hybrid: 3 of 7 labelled cases with an expected passage in the top five; in the run itself 5 of 7 had at least one expected locator among the five: PC01, PC03, PC05, PC09, PC15). Judgment: the id tokens are noise, probably harmless, but the row and the run should use the same query. Changing it changes retrieval, so it is for a decision before the freeze, not a quiet edit. Row 1 also counts a hit by document and locator, not by chunk, so PC09 is a hit by locator and a miss by content.

### 8.4 Scoring (development set, pre-freeze)

Command (no model call): `gapcourt score --kb build/kb --labels evals/pilot_cases_DRAFT.jsonl --runs runs --split dev --out results/dev_2026-11-02`. Stamp: draft labels, no freeze record, scoring code `e9943e5`. Counted runs: the dev run and S-03; the smoke `dev3` run is listed as not counted (a smaller dev run). Output: `results/dev_2026-11-02/results.md`.

| | System | Baseline |
|---|---|---|
| Outcome agreement, all 10 | 6 of 10 | 3 of 10 |
| answerable | 3 of 3 | 1 of 3 |
| paraphrase_trap | 0 of 2 | 0 of 2 |
| missing_document | 1 of 2 | 2 of 2 |
| ambiguity | 1 of 2 | 0 of 2 |
| candidate_gap | 1 of 1 | 0 of 1 |
| Gap stated where the label has an answer | 2 of 5 | 0 of 5 |
| Answer given where the label has a gap or abstention | 0 of 3 | 0 of 3 |
| Quotations kept / dropped (dev run plus S-03) | 9 / 0 | |
| Model rejections | 0 | |

How to read it: n is 10 and the labels are drafts. The prompts were revised after the smoke session looked at PC01, PC03 and PC11, so those three are not independent; on the other seven the system agrees on 3 (PC05, PC15, PC19) and the baseline on 1 (PC13). If GAP and ABSTAIN were one class, PC13 would agree and the system would reach 7 of 10, but that is exactly the open label-design question, so 6 of 10 is the number that stands. The baseline's 2 of 2 on missing_document comes from its design: it abstains on 9 of the 10 cases and answers only PC03. No confidence interval is claimed at n of 10. None of these figures belongs in the report as a result; held-out numbers need approved labels and the freeze.

### 8.5 Open items from the dev run

- Label review: the reviewer still has to decide the label questions raised above.
- Arabic-only U01 and U08 are listed as reviewed but cannot be retrieved from English questions; the "Reviewed instruments" line should reflect that, or the label convention should.
- Query prefix `[case ID]` in retrieval versus the score row: decide before the freeze.
- Report requirement R4 ("revisions 0 [measured]") reads as if revision behaviour had been tested. No run so far contains a contradicting step (score row 5 is 0 of 0), so the wording should say untested until S-01 and S-02 are run.
