# Corpus provenance: how each corpus file relates to its raw source

Checked Sun Nov 1, 2026 with `gapcourt prepare` (raw files in place, all hashes matching). Result: 10 of 19 corpus files rebuild
byte for byte from their raw file by a recorded transform; the other 9 pass the verbatim audit; none fails. Evidence tag **V**:
the command was run and its output is in `corpus/PROVENANCE_AUDIT.json` (text-free: statuses, counts and line hashes only).

## What was wrong

The corpus files were made on Oct 27 by throwaway extraction scripts that were not kept. `gapcourt prepare` could rebuild only U01.
For the other 18 files nothing recorded how they differed from their sources, so a judging claim resting on one of them could not
be checked by script.

## What the check does now

`gapcourt prepare` gives every corpus file one of two passing statuses and says which:

- **REPRODUCED.** A recorded transform rebuilds the file byte for byte from the raw file whose sha256 its front matter records:
  an extractor (`pymupdf_lines` or `html_blocks`), its options (drop blank lines, drop running headers), and a patch of guarded
  hunks stored in `corpus/PREP.json`. Each hunk has a class and a hash guard: the patch refuses to apply if the line it goes
  before, or the lines it deletes, are not the recorded ones. A patch for a link-only or unknown-terms text may record structure
  and fragments of at most 12 characters, never source text (`test_link_only_transform_may_not_store_source_text_in_the_patch`).
  `scripts/derive_prep.py` is the record of how the patches were derived from the existing files.
- **AUDITED.** No byte-exact transform exists (curated excerpts that join wrapped lines, flatten tables, or draw on several raw
  files), but every content line of the file is found in the text of the hash-verified raw files (the file whose sha256 is in the front matter, plus siblings that are listed in the folder's `SHA256SUMS.txt` and match their bytes; any other file is ignored and named in the audit as `unverified_raw_files_ignored`) under one of five named
  normalizations (each matched line is matched against one raw file at a time, never stitched across files, and the audit records which file supplied it: `matches_by_source` in `corpus/PROVENANCE_AUDIT.json`), and any line that is not would have to be a registered edit in `PREP.json["accepted_edits"]`. There are no
  registered edits and no unmatched lines. This is a statement about the words, not the bytes: it does not prove that the
  excerpt chose the right passages or kept them in source order.

The five audit tiers, weakest last: `verbatim` (whitespace collapsed), `whitespace` (equal with all whitespace removed, for
inline markup), `typography` (curly quotes, dashes, non-breaking spaces folded), `enumerator` (a leading list number or bullet
that the source renders from list markup or a separate line), `labelled` ("Label: text" from a flattened table row or a
key-value rendering, where label and text are each found in the raw text).

`gapcourt prepare --rebuild-all` writes the 10 reproducible files in a fresh clone after `gapcourt kb-fetch` (checked from a
fresh folder on Nov 1: U01, U06, U08, U11, U12, U13, U15, U16, G6 came out byte-identical to the working copies; G7 is in the
public tree).

## Classification of every difference

Three kinds of difference were looked for. "Source drift" would be a raw file that no longer matches its recorded hash.

| Kind | Finding |
|---|---|
| Hand edit of text | None found. Across the 9 audited files, 0 content lines are outside the raw text. Across the 11 reproduced files, the only differences from a plain extraction are the hunks below. |
| Normalization | Wrapped lines joined; list numbers and bullets rendered from markup; table rows flattened to "Label: text"; running page headers and blank lines dropped; Word-HTML table rows joined as "term definition". |
| Structural additions by the preparer | `### Article N` / `### Section N` / `### Principle N` / `### Part: ...` headings, `<!--page:N-->` markers, `[...]` omission marks. These are the locators the passage ids use. Headings whose number does not literally appear in the raw text are listed in the audit under `headings_unverified` (G1, G3, G5, G8, F1, F2 after the 2026-11-01 review made the check cover every heading kind, not only Article, Section, Clause, Principle and Part); they are labels, not quotations. |
| Excerpt selection | G1, G2, G2A, G3, G4, G5, G6, G7, G8, F1, F2 are excerpts, titled so. G6 and G7 record the selection as hash-guarded deletions. |
| Source drift | None for the 20 files: each raw file matches the sha256 in its front matter. Two sources did not serve the recorded bytes to a plain client on Nov 1 (G1 HTML page, G6 PDF HTTP 202) and are marked manual in `corpus/SOURCES.json`; that is a retrieval risk, not a text change. |

| Doc | Status | How | Detail |
|---|---|---|---|
| U01 | REPRODUCED | Python recipe (pypdfium2, Arabic headings) | existing since Oct 27; Arabic text-layer ligature defects, unsuitable for English quotation checks |
| U01EN | REPRODUCED | `pymupdf_lines`, running header and page numbers dropped | 31 heading hunks (Articles 1 to 31, once each); official English text from the UAE Legislation portal, downloaded by the reviewer in a browser on 2026-11-09 (the portal refuses a scripted client), sha256 `d5d5f9e9...12bcf3a`, 29 pages, link only; added 2026-11-09 |
| U06 | REPRODUCED | `pymupdf_lines`, running header dropped | 21 heading hunks |
| U08 | REPRODUCED | `text_lines` on the private checked transcription (see below) | 9 heading hunks (Articles 1 to 9, once each); fallback when the transcription is absent: `pymupdf_lines` on the official PDF, 11 heading hunks |
| U11 | REPRODUCED | `pymupdf_lines`, running header dropped | 21 heading hunks |
| U12 | REPRODUCED | `html_blocks` | 47 heading hunks, 1 deletion (a decorative tatweel rule line) |
| U13 | REPRODUCED | `html_blocks` | 11 heading hunks |
| U15 | REPRODUCED | `pymupdf_lines` on the .docx | 34 heading hunks (`Principle N`, `Part:`) |
| U16 | REPRODUCED | `pymupdf_lines` on the .docx | 9 heading hunks |
| G6 | REPRODUCED | `pymupdf_lines` | 9 excerpt deletions, 5 omission marks |
| G7 | REPRODUCED | `pymupdf_lines` | 14 excerpt deletions, 1 hunk joining two source lines (public tree, so text may sit in the patch) |
| G1 | AUDITED | 185 lines: 41 exact, 39 whitespace, 67 enumerator, 38 labelled | HTML list and table flattening |
| G2 | AUDITED | 153 lines, all exact | Cellar XHTML selection; page markers from the Cellar PDF |
| G2A | AUDITED | 25 lines, all exact | amending points for Articles 27, 111, 113 only |
| G3 | AUDITED | 130 lines, all exact | recitals and articles selection |
| G4 | AUDITED | 43 lines: 13 exact, 30 enumerator | numbered paragraphs rendered "1." / "(a)" |
| G5 | AUDITED | 29 lines, all exact | OECD body HTML plus metadata JSON |
| G8 | AUDITED | 49 lines, all exact | XML of SGB X and VwVfG sections, German |
| F1 | AUDITED | 40 lines, all exact | Y.3172 clauses 3.2 and 8.x excerpt |
| F2 | AUDITED | 30 lines: 15 exact, 13 enumerator, 2 labelled | 13 dimensions list; the key-value block is rendered from the reference repository's `dimensions.json` |

## What is still not byte-reproducible, and why

Nine audited files need their joined or flattened text stored somewhere to be rebuilt exactly. For G1, G4, G5, G8, F1 and F2 (link-only
or unknown terms) that would put source text into the repository, which this project does not do, so they stay under the
verbatim audit. G2, G2A and G3 are public, but their Cellar XHTML selection takes page markers from a second raw file and needs
a larger extractor than the time allowed; they stay audited. Consequence for the report: build-kb from a fresh clone reproduces a
smaller KB (12 documents on 2026-11-10 without the browser downloads); the full 20-document KB version used in the live
runs (kb-6b62e9ecc91c) is recorded in each run's summary and published as `kb_release/`, and the report says which one a number refers to.

## U08: checked transcription, not the official text layer (added 2026-11-09)

The official U08 PDF (sha256 `9bb60e43...f448d0`, unchanged) has a text layer with lam-alef ligature-order defects, so a
correctly spelled Arabic quotation failed the exact check against it. Since 2026-11-09 the knowledge-base text of U08 is a
**checked transcription, not the official text layer**, and the quotation check for U08 runs against it.

- **Method: two independent methods, merged; never either alone.** (1) Deterministic repair of the PDF text layer, no
  model: tatweel and tashkeel stripped, the detached ligature merged, each alef+lam pair tried in both orders against an
  Arabic frequency lexicon. (2) Image transcription of each page by Codex (image only, the text layer not shown to it).
  The image transcription gives line breaks and reading order; every word on which the two disagree (bag of words per
  page) was resolved against the page image by eye and logged.
- **Disagreements: 12 in 2,325 words, all resolved.** The image transcription was wrong 8 times (confident
  autocorrections, none marked uncertain; two of them "restored" words that the official PDF misprints, which the
  transcription keeps as printed); the lexicon repair was wrong 4 times (homographs, a word-initial pair, one heuristic
  flip). After resolution: 0 unexplained disagreements, and an order check finds every line's Arabic words in the text
  layer's own order (233 of 244 lines or table cells whole; 11 split only at numbers or Latin, where the layer's
  right-to-left order around digits differs).
- **Conventions:** tashkeel and tatweel are not kept (the two methods were compared without them; a quotation with
  tashkeel still passes the letter-set check as a reading); definition-table rows are "term TAB definition"; the
  cover's ministry header and the back page's social handle exist only in the image and are left out.
- **Hash and files:** transcription sha256 `c585b868...3f39e`, `corpus_local/_raw/U08/U08_checked_transcription.txt`, listed
  in that folder's `SHA256SUMS.txt` and in `corpus/SOURCES.json` (manual, private). It stays private like the other UAE
  texts (link-only terms of the Cabinet Office). Script: `scripts/build_u08_checked.py` (it stops if any disagreement is
  unreviewed). The word-level log with the evidence for each resolution is internal and not exported.
- **Fresh clone:** without the private transcription, `gapcourt prepare --rebuild-all` rebuilds U08 from the official
  PDF's text layer (the `fallback` transform in `corpus/PREP.json`) and says so; that knowledge base has a different
  version, and Arabic quotations from it verify only by the letter-set check.
- **Unchanged:** U08 is Arabic, so by rule R1 a U08 citation is a reading and never carries ALLOW or DENY. The limits: a
  word wrong in both methods in the same way would not show as a disagreement; tashkeel is not verified.
