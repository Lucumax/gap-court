# Gap Court results

- split: heldout; labels: labels_v1.jsonl sha256 `8ba759817e35d38fd70fae5a343d789ac676482cfe54b71d6da9edf9923be652` (APPROVED (frozen))
- freeze record: `e4afb616c830286fb249b2ba7d79d22534a50e0007f2fca3061dbad8a5d73a6d`
- KB version: `kb-6b62e9ecc91c`; models: claude-haiku-5-5; effort: medium
- code: scoring c0cf7ac+dirty; runs 82c4576, c0cf7ac+dirty, fa2335b
- runs: heldout/20261110T092416Z@da6ecbdc396a, smoke/S-03-20261102T014545Z@c0dd303a173a, demo/S-04/r1@eae7d8a413df, demo/S-04/r2@d3986e2a0d45, S-01/r1@3b8f4cf4d9d9, S-01/r2@94dbd81a3485, S-01/r3@71758d66f029, S-02/r1@7d9aa86b6519
- **Code differs from the frozen code for: smoke/S-03-20261102T014545Z@c0dd303a173a (the code tree differs from the frozen one (freeze 3e6ebd9d7a09, run 9d9c15188a27)); demo/S-04/r1@eae7d8a413df (the code tree differs from the frozen one (freeze 3e6ebd9d7a09, run fb4718023db3)). These runs are counted with this label; they are not strictly under the freeze.**

## 1. Governing passage in top 5

| case type | BM25 | vector | fused |
|---|---|---|---|
| all | 1 of 4 | 2 of 4 | 2 of 4 |
| ambiguity | 0 of 1 | 0 of 1 | 0 of 1 |
| answerable | 1 of 2 | 2 of 2 | 2 of 2 |
| paraphrase_trap | 0 of 1 | 0 of 1 | 0 of 1 |

## 2. Outcome agreement with labels

Run: heldout/20261110T092416Z@da6ecbdc396a. Baseline: retrieval plus abstain threshold (no roles, no gate).

| case type | full system | baseline |
|---|---|---|
| ambiguity | 0 of 1 | 0 of 1 |
| answerable | 2 of 2 | 0 of 2 |
| candidate_gap | 2 of 2 | 0 of 2 |
| missing_document | 2 of 2 | 2 of 2 |
| paraphrase_trap | 1 of 1 | 0 of 1 |
| all | 7 of 8 | 2 of 8 |

Blocked on a missing source text, so not run or scored: PC04, PC10.

## 2b. Paired comparison on identical cases

same cases, same knowledge base, same retrieval settings (top 5, kb_uae); the baseline calls no model, so it has no model failures and never escalates.

| count of cases | full system | retrieval plus abstain |
|---|---|---|
| cases | 8 | 8 |
| outcome agreement (same label as the approved one) | 7 | 2 |
| decisive recommendations | 3 | 0 |
| unsupported decisive recommendations | 0 | 0 |
| escalations | 1 | 0 |
| abstentions or gap findings | 4 | 8 |
| model failures | 0 | 0 |

Unsupported decisive: answered where the approved label expects another outcome. Counts show pilot agreement on a few cases; they do not establish legal correctness.

## 3. False gap findings and false answers

| | full system | baseline |
|---|---|---|
| gap where the label has an answer | 0 of 4 | 0 of 4 |
| answer where the label has a gap or abstention | 0 of 4 | 0 of 4 |

## 4. Scenario steps matching the expected ruling

| run | scenario | matched | expected rulings frozen | earlier run before freeze |
|---|---|---|---|---|
| smoke/S-03-20261102T014545Z@c0dd303a173a | S-03 | 1 of 1 | no | yes |
| demo/S-04/r1@eae7d8a413df | S-04 (development demonstration, reported separately) | 0 of 2 | no | yes |
| demo/S-04/r2@d3986e2a0d45 | S-04 (development demonstration, reported separately) | 0 of 2 | no | yes |
| S-01/r1@3b8f4cf4d9d9 | S-01 | 2 of 3 | yes | no |
| S-01/r2@94dbd81a3485 | S-01 | 2 of 3 | yes | no |
| S-01/r3@71758d66f029 | S-01 | 2 of 3 | yes | no |
| S-02/r1@7d9aa86b6519 | S-02 | 1 of 2 | yes | no |

## 5. Revisions after contradicting steps

| run | revised | stale rulings left standing |
|---|---|---|
| smoke/S-03-20261102T014545Z@c0dd303a173a (S-03) | 0 of 0 | 0 of 0 |
| demo/S-04/r1@eae7d8a413df (S-04 (development demonstration, reported separately)) | 0 of 1 | 1 of 1 |
| demo/S-04/r2@d3986e2a0d45 (S-04 (development demonstration, reported separately)) | 0 of 1 | 1 of 1 |
| S-01/r1@3b8f4cf4d9d9 (S-01) | 0 of 2 | 2 of 2 |
| S-01/r2@94dbd81a3485 (S-01) | 0 of 2 | 2 of 2 |
| S-01/r3@71758d66f029 (S-01) | 0 of 2 | 2 of 2 |
| S-02/r1@7d9aa86b6519 (S-02) | 0 of 0 | 0 of 0 |

## 6. Quotations

Kept 50, dropped 1 (by reason: {"quotation_too_short": 1}). kept means the quotation was found in the cited passage; it is not a legal interpretation.

## 7. Model rejections, tokens and cost

Rejections by reason: {}; calls 35; input tokens 129294; output tokens 38324; USD 0.0321.

## 8. KB release

no KB-release receipt saved under the runs folder
