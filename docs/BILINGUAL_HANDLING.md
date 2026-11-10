# Arabic and English: what this build does and does not do

Status: a statement of the current build, not a feature. Questions, scenarios, prompts and answers are English. Since 2026-11-02 there is one narrow, model-free retrieval path that lets English questions reach the two Arabic-only instruments (U01, U08); it is a retrieval aid and nothing else. Nothing here claims Arabic quotation checking or a translation.

## Why it matters

UAE instruments are issued in Arabic; the English versions carry a note that Arabic prevails in a conflict (recorded per document in `corpus/APPLICABILITY.json`, field `language_note`: U06, U11 and U12). A decision aid that quotes an English text is therefore quoting a courtesy translation for those documents. Gap Court says so and never presents an English quotation as the controlling wording.

## The knowledge base, language by language

| Document | Language in the knowledge base | Handling |
|---|---|---|
| U06, U11, U12, U13, U15, U16 | English official versions (Arabic prevails where the footnote says so) | Retrieved and quoted; the quotation badge links to the official source and the report says Arabic prevails |
| U01 (federal personal data decree-law) | Arabic print edition only | In the knowledge base for reading and for the scope finding (Article 2 exclusion of government entities, read by the author on the Arabic text). The PDF's own text layer has ligature-order defects (checked on 2026-11-01: a second extractor gives the same result), so no verified English quotation is possible. The English text is a manual download (open item) |
| U08 (digital customer and digital government service policy) | Arabic only | In the knowledge base; reachable from English questions only through the retrieval path below |

Rules the build applies:

1. No machine translation of legal text is used as evidence anywhere. A paraphrase in the dossiers is marked as the author's reading.
2. A finding that rests on an Arabic-only text is marked "unresolved" or "reading confirmed on the Arabic text; English wording not obtained" until an official English text is in the knowledge base.
3. The quotation check compares strings after Unicode normalisation (NFKC) and whitespace folding. It was not designed or tested for Arabic script; do not read it as bilingual verification.
4. Questions and scenario steps are English. The model may be able to read Arabic; the build does not test that and makes no claim.

## Retrieval path for English questions

Why. U01 and U08 exist in the knowledge base only as Arabic text, so an English question shared almost no words with them: on 15 author-made questions about their content only 2 of 15 reached the right article in the top five (the two hits came through an English title match and an Arabic question). The first live dev run showed the effect: the case adviser was told U01 was reviewed and could not retrieve it (PC13, PC07, PC17, PC19).

First choice was an official English text. U01 has one on the UAE legislation portal (`uaelegislation.gov.ae/en/legislations/1972`); the portal answers a scripted client with HTTP 403 (checked again on 2026-11-02 with a plain fetch and a page-fetch tool), `u.ae` offers U01 in Arabic only, and nothing else official in English was found. Nothing was obtained, so no English text was added and no hash recorded. The manifest entries (U01EN, U04, U10) stay manual (browser download). If the English text arrives this path remains useful for U08 and as a cross-check, and it can be switched off per retriever (`bilingual=False`).

What the path is (`src/gapcourt/bilingual.py` and `bilingual_map.py`; deterministic, no model):

1. Letter-set key. Arabic tokens are indexed and matched by a key made of their letters after dropping diacritics and tatweel, folding hamza-carrying alef, final yeh and teh marbuta, and sorting the letters. This makes the ligature-order defects of the text layers harmless (the two spellings of "the processing" share a key). Cost: anagrams share a key; the role still reads the passage.
2. Concept map. Each concept lists English trigger phrases in groups (every group must match, so "government" alone never fires the scope concept), the Arabic search terms to add, and the article where the author read them. A test checks that each term occurs in its target chunks of the locally built knowledge base. The map is a retrieval aid written by the author; it is never shown to a role as the law, never quoted and never evidence of what a provision says. Questions that use other words for an idea (for example "AI-assisted" for "automated") do not trigger it; that limit is deliberate, because widening a trigger toward the wording of a question is tuning. `gapcourt expand-query --query "..."` prints what a question triggers, so a retrieval in a saved run can be reproduced (the map hash is in the retriever configuration recorded with each step).
3. Reserved slots. The vector arm cannot rank an Arabic passage against an English question, so one fused list either buries the passage or, if it borrows a rank, floods the top five (tried first: three dev questions got an almost all-Arabic top five and one lost its expected English passage). When the map fires, at most 2 of the 5 slots go to the Arabic-only passages that best match the added terms alone, interleaved with the usual list; the other passages keep their order. When the map does not fire, ranking is identical to before (tested on a fixture, and on the real knowledge base: with `bilingual=False` the top five of all 10 dev questions and of the 14 English probes equals the top five at 0198f0d; only the one Arabic probe, B15, differs, because the new tokenizer keys Arabic words). Scores: a slot passage carries the reciprocal-rank value of its place in the quota, not a fused score, so scores are not comparable between a slot passage and the others and the returned order does not always follow `Hit.score`; the saved record marks such a passage with `via: arabic_quota`. While the map fires, every Arabic-only passage is taken out of the usual list and only the best two by the added terms come back, so an Arabic passage that the question's own words ranked high can be left out; this is the cap, stated.

Measured offline, no model call (`results/bilingual_retrieval_before.json` at 0198f0d, `results/bilingual_retrieval_after.json`, script `scripts/bilingual_probe.py`):

| Check | Before | After |
|---|---|---|
| 15 author-made probe questions, right article in the top five (14 English, 1 Arabic) | 2 of 15 | 15 of 15 |
| 10 dev questions, expected passage in the top five (7 have one; unapproved draft labels) | 3 of 7 | 3 of 7 |
| dev questions with a U01 or U08 passage in the top five | 0 of 10 | 3 of 10 (PC05, PC13, PC17; 2 slots each) |

How to read this. The probes were written by the same author who wrote the map, and the map was adjusted after the first run missed two of them (the in-force and policy-scope concepts), so 15 of 15 shows that the machinery works, not that it generalises. It is a retrieval figure, not an accuracy figure. Held-out questions were not run for this work and `scripts/bilingual_probe.py` drops held-out lines before parsing, so it loads none. Chronology from the git history: the map and the quota were last changed at 10:48 on 2026-11-02 (11177e3); the held-out cards were first read for the freeze-day plan at 11:17 (9eaaf5e), which noted that questions worded "AI-assisted" (PC19 in dev, PC20 held-out) do not trigger the map, and left the map unchanged. Whether the author saw held-out wording before that is the author's record, not something the history can prove. The retrieval effect on held-out questions is unmeasured and will first show in the held-out run. The dev expected-passage count did not move, which is the no-harm check.

What it does not fix. A passage that arrives this way still cannot support a verified English quotation, and an Arabic passage never carries a ruling. Since gate v2 the gate enforces R1: an Arabic citation is kept and recorded with its language, but a ruling (ALLOW or DENY, which scores as `ANSWER`) needs a verified binding citation whose text is not Arabic, or it is downgraded to ESCALATE with a note. Since gate v3 the gate also accepts, for an Arabic passage only, a quotation that fails the exact string check but passes a letter-set check (`bilingual.check_quotation_letter_set`: whole words in order, compared through the letter-set key, at least 4 Arabic words, every ellipsis fragment at least 2). That helps a model that quotes correctly spelled Arabic against a text layer with ligature-order defects, which the exact check rejected. Such a citation is marked `quotation_check: letter_set`, and every Arabic citation (exact or letter-set) carries `reading_only: true`; the same marks go on a scope finding. The check is weaker than the exact one (letter order inside a word is ignored, so an anagram of the right letters would pass; four words in a row make an accidental match very unlikely but not impossible), which is why the result is a reading and not a quotation of the law. In a dossier the claim prints "letter-set match, reading only" and an Arabic passage still supports only a `reading` or an `unresolved` finding. A finding that rests on U01 therefore stays "reading confirmed on the Arabic text; English wording not obtained" and cannot be an `ANSWER`; the check does not unblock PC13, PC16 or S-02 step 2 (only the official English U01 text does that). Any report wording should say "matched by letter set (a reading)" for such a citation.

## What would change this (not done)

- Official English text of U01 (a browser download), then verified quotations against it become possible.
- A different official Arabic source or OCR for U01 and U08 to repair the text layer, and a full Arabic query path with its own tests (estimated about six hours; not started). The English-question path above is much narrower than that.
- A bilingual evaluation case set with an Arabic-speaking reviewer. Not available in this entry.

This is listed under limitations in the report: "English-language questions; Arabic-only texts are reached through a documented lexical concept map, have text-layer defects and cannot support verified English quotations."
