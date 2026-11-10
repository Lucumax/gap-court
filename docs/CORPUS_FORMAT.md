# Corpus file format

One document per file. Collections: `uae` (kb_uae), `global` (kb_global), `framework` (kb_framework).

Location:
- `corpus/<collection>/<DOC_ID>.md` when redistribution is permitted (terms text recorded in `terms_note`).
- `corpus_local/<collection>/<DOC_ID>.md` when the status is link-only or unknown (git-ignored; used locally, never published).
- Raw downloads go to `corpus_local/_raw/<DOC_ID>/` (git-ignored) so `sha256` can be recomputed.

File layout: a front-matter block, then the body.

```
---
doc_id: U01
instrument_id: AE-FDL-45-2021
title: Federal Decree-Law No. (45) of 2021 on the Protection of Personal Data
issuer: President of the United Arab Emirates
instrument_type: decree-law
binding: true
jurisdiction: AE-federal
language: ar
official_translation: false
source_url: https://...
retrieved_at: 2026-10-27T14:03:00Z
version: first edition (Dubai Judicial Institute print, 2023)
effective_date: 2022-01-02
supersedes: null
status: in force
terms_note: link only; no republishing terms found
republish: link_only
sha256: <sha256 of the raw downloaded file>
extraction: pymupdf 1.28.0, text layer; Arabic ligature order defects noted
---
<!--page:5-->
### Article 1
Body text, verbatim from the source. Never machine-translated.

### Article 2
1. ...
```

Rules:
- Front-matter values are single-line `key: value`; `null`, `true`, `false` are literals; everything else is a string.
- Required keys: `doc_id, instrument_id, title, issuer, instrument_type, binding, jurisdiction, language, official_translation, source_url, retrieved_at, version, effective_date, supersedes, status, terms_note, republish, sha256`.
- `republish` is one of `yes`, `link_only`, `unknown`.
- Locators in the body: a page marker line `<!--page:N-->` (N is the page of the source, as printed or as indexed, say which in `extraction`) and structural headings `### Article N`, `### Section N`, `### Clause N`, `### Principle N` or `### Part: <name>`. Text before the first heading is the preamble.
- Body text is verbatim. Where the source text is a rendering of an HTML page, keep the wording and drop navigation. Mark any omission with `[...]`.
- Do not add commentary inside the body. Commentary belongs in `dossiers/` or in the applicability record.
