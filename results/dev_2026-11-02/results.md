# Gap Court results

- split: dev; labels: pilot_cases_DRAFT.jsonl sha256 `732fdb4c54cfcf38dac48ccc20fb6978fa9db85a3b6e5b99f52ff977905cb066` (DRAFT_UNAPPROVED labels present: machinery check, not a result)
- freeze record: none
- KB version: `kb-b08e7cfac2df`; models: claude-haiku-5-5; effort: medium
- code: scoring e9943e5; runs 1c4c063, fa2335b
- runs: dev/20261102T102235Z@c02e09357f10, smoke/S-03-20261102T014545Z@c0dd303a173a

## 1. Governing passage in top 5

| case type | BM25 | vector | fused |
|---|---|---|---|
| all | 3 of 7 | 3 of 7 | 3 of 7 |
| ambiguity | 1 of 2 | 0 of 2 | 0 of 2 |
| answerable | 1 of 3 | 3 of 3 | 3 of 3 |
| paraphrase_trap | 1 of 2 | 0 of 2 | 0 of 2 |

## 2. Outcome agreement with labels

Run: dev/20261102T102235Z@c02e09357f10. Baseline: retrieval plus abstain threshold (no roles, no gate).

| case type | full system | baseline |
|---|---|---|
| ambiguity | 1 of 2 | 0 of 2 |
| answerable | 3 of 3 | 1 of 3 |
| candidate_gap | 1 of 1 | 0 of 1 |
| missing_document | 1 of 2 | 2 of 2 |
| paraphrase_trap | 0 of 2 | 0 of 2 |
| all | 6 of 10 | 3 of 10 |

## 3. False gap findings and false answers

| | full system | baseline |
|---|---|---|
| gap where the label has an answer | 2 of 5 | 0 of 5 |
| answer where the label has a gap or abstention | 0 of 3 | 0 of 3 |

## 4. Scenario steps matching the expected ruling

| run | scenario | matched | expected rulings frozen | earlier run before freeze |
|---|---|---|---|---|
| smoke/S-03-20261102T014545Z@c0dd303a173a | S-03 | 1 of 1 | no | no |

## 5. Revisions after contradicting steps

| run | revised | stale rulings left standing |
|---|---|---|
| smoke/S-03-20261102T014545Z@c0dd303a173a | 0 of 0 | 0 of 0 |

## 6. Quotations

Kept 9, dropped 0 (by reason: {}). kept means the quotation was found in the cited passage; it is not a legal interpretation.

## 7. Model rejections, tokens and cost

Rejections by reason: {}; calls 11; input tokens 39425; output tokens 8149; USD 0.0080.

## 8. KB release

no KB-release receipt saved under the runs folder

## Runs not counted

- smoke/dev3-20261102T014545Z@7282a1b5219e: another dev run; the latest run covering the most dev cases is the one reported
