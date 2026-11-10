# Known limitations

This file lists what Gap Court does not do, what it has not tested and what it cannot show. It is kept under the old file name `NOT_DONE.md` because other pages link to it.

## What the results can and cannot show

- The test set is small and was labelled by one reviewer. The scenarios are invented for this entry. Agreement with the reviewer's labels is a development check. It is not proof that the system is accurate.
- The quotation check shows that the quoted words appear in the cited passage. It does not show that the passage supports the conclusion, that it applies to the actor, or that the law is read correctly. A person must judge that.
- The applicability check shows that a reviewer approved a record for the cited instrument and that the model said it applies. It does not show that the model's "yes" is right. A model that wrongly says yes for the wrong actor still passes.
- The rule that maps a case result to a label outcome (ALLOW or DENY with a verified quotation counts as ANSWER; a kept scope finding counts as SCOPE_FINDING) is a design choice in `src/gapcourt/cases.py`. A reviewer should confirm it.
- In the live scenario runs every S-01, S-02 and S-04 step was ruled ESCALATE. The system did not reach the DENY and ALLOW rulings the labels expect and made no revision after a new fact; in one S-01 run the records custodian proposed DENY with checked quotations, but the coordinator rule keeps the legal adviser's assessment. Escalating is the safe default, but it is not shown that the system can reach a supported refusal or approval.
- Each result comes from one model, one set of settings and few runs. The settings (structured output, summarized thinking display, effort medium) were accepted in the saved runs; that does not guarantee they are accepted in other sessions.
- The hash-chained log shows that a record was changed, removed or reordered. It does not show who wrote it or whether it is true. To show that a log was cut short, the digest must be kept separately from the log.
- The defence against instruction-like text inside a quoted passage is a short pattern list. It is narrow and not a guarantee.

## Knowledge base

- The default build uses a local word-based search model (`hashing-lexical-v1`), not a neural model. The ITU reference code is vendored and its embedding and store functions are tested against the originals with test doubles. A run with Ollama and `nomic-embed-text` (`gapcourt build-kb --embedder ollama --chroma-dir <dir>`) has not been done. The default environment has no `chromadb`, so the real round-trip test is skipped there.
- Ten of the 19 corpus files rebuild byte for byte from their raw files. The other nine are excerpts that pass the check that every line appears in the source, and no more than that (`docs/CORPUS_PROVENANCE.md`).
- A fresh clone rebuilds a smaller knowledge base: on 2026-11-10, with network access and no browser downloads, the README quickstart gave 12 documents (kb-04d391c100e3). The live runs used the 20-document kb-6b62e9ecc91c, published as `kb_release/` without link-only text. The report states which version each number refers to.
- Four open documents (G2, G2A, G3, G7) have no entry in `corpus/SOURCES.json`. In a fresh clone `gapcourt prepare` reports them as RAW_MISSING. Their text is in the repository, but its link to the raw file can only be checked where `corpus_local/_raw` exists.
- The texts U04 and U10 were not obtained. The UAE legislation portal refuses a scripted client with HTTP 403. The official English text of U01 was downloaded by hand in a browser (2026-11-09) and is in the knowledge base as U01EN; a fresh clone needs the same browser step. U02 and U03 are out of scope.
- English questions reach U01 and U08 through a word map (`docs/BILINGUAL_HANDLING.md`, `gapcourt expand-query`). This is retrieval only. It gives no verifiable English quotation, and the effect on held-out cases was not measured in advance.
- The Arabic-only U01 and U08 text layers have encoding defects in the PDF itself, so they cannot support verified English quotations. The check by Arabic letter set accepts a correctly spelled Arabic quotation as a reading only. It cannot support ALLOW or DENY, and its benefit on model output is not established.
- The sources G1 and G6 did not serve the recorded file to a plain client and must be downloaded by hand. The HTML sources (U12, U13, G1, G5) can change between visits, so a hash mismatch needs a review.
- In the PDF-sourced Dubai texts (U06, U11), definition tables extract with each defined term after its definition. The corpus file headers say so; the role prompts do not.
- Scenario S-02 step 2 (scope finding on the federal personal data law) rests on U01EN Article 2; the reviewer accepted it before the freeze, and the live run escalated the step instead of making the scope finding.

## Short quotations from link-only texts

Most UAE instruments are link-only under their publishers' terms, so their full text is not in this repository. Short verification quotations (each with a locator) appear in the labels, dossiers, generator scripts and the gate records and trace pages of saved runs. The export refuses any passage longer than 40 words of link-only text and lists every file that quotes. In the held-out run the model quoted 56 words of one link-only article (U12 Article 34) and the quotation verified; that run's log and trace page are therefore left out of the public copy (`config/export_withheld.json`, `scripts/release_export.py`) and kept in the working repository, where they verify against the digest that is published. Whether short verification quotations may be published at all is a question for the text owners; the alternative is to remove them, which would make the labels and dossiers unreadable without the local texts.

## Costs

The cost caps hold under stated assumptions, not unconditionally. Before each call the code reserves an upper bound (the request's UTF-8 bytes plus 2,000 tokens at the input price, plus `max_tokens` at the output price) and sends the call only if the total fits under both caps. A crash during a call stays reserved and is carried into the next run's total. The code does not enforce three assumptions:

- The price table comes from the Claude API reference, cached 2026-10-06, and was not checked against the live pricing page. A price understated by a factor f can exceed a cap by up to (f minus 1) times the cap.
- The provider adds no more than 2,000 tokens of its own around the request.
- Nothing else spends on the same key. The limit set in the provider's console is the backstop.

`gapcourt cost` prints this.

## Source dossiers

The dossier `docs/DOSSIER_S01.md` is checked by `gapcourt dossier-check` (the quotations are found in their passages, the chunk and raw-file hashes are recomputed). Its conclusion is preliminary. Comparator texts are read from the knowledge base, and a human reviewer has not re-opened them on the primary source (four comparator quotations were re-opened by an automated fetch). Two of its findings are readings of Arabic text. The skeleton dossiers D1 and D3 are unresolved evidence with no supported policy finding.

## Not included

There is no explainer video.
