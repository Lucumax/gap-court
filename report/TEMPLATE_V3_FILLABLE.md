<!--
Fillable source of the five-page technical document. `gapcourt report-fill` turns it into docs/REPORT_DRAFT_5PAGE.md: every {{fact:...}} becomes a value with a tag
[measured] (from a saved run that called the model), [offline] (KB build, provenance check, dossier check, retrieval check, offline tests) or [pending-live: needs the X session].
No number is written by hand in the prose. Square-bracket fields are supplied by the author before sending. Page budget per section in the headings.
Official template structure (Word template from the event page, downloaded 2026-11-02): title line, team fields, then numbered sections Introduction; Description of the use case;
Use case requirement; Mapping with ITU-T Y.3172 Readiness 2.0 Report and domain-specific policies (table: node, use case, Readiness 2.0, documents); Error scenarios; Policy gaps and
standards gaps. `scripts/build_report_docx.py` renders this filled draft into the official template as report/REPORT_v1.docx.
-->

# Gap Court: a prototype that checks quoted support for policy statements and records what it cannot settle   [header block, 0.3 page]

A federal benefit service wants to reuse household records that a Dubai office collected for housing registration. What should a human officer review before allowing it? Gap Court simulates four institutional roles, checks that quotations appear in the cited passages, traces recommendations to sources and sends unresolved questions to a person.

{{headline: held-out outcome agreement, full system vs baseline, copied from results/heldout/results.md section 2 by scripts/compact_results.py}}

Team leader: Walter Guevara (team Juncture Policy, solo entry). Team members: none. Contact details: given in the submitted report. GitHub link: this repository (MIT licence).
Independent entry, with no ITU or UAE authority affiliation or endorsement. Built with LLM-assisted development (an AI language model) that I specified, directed and reviewed. Code for loading sources adapts the ITU reference repository (CrashingGuru/ITUAIReadiness, MIT licence; changes in NOTICE). Claude Haiku 5.5 (`claude-haiku-5-5`) writes the roles' responses; every result names it.

## 1. Introduction   [0.2 page]

Gap Court follows one simulated public service process across texts that differ in scope, legal force and level of government. At each step a role searches a knowledge base of {{fact:kb.documents}} public instruments ({{fact:kb.chunks}} passages, version {{fact:kb.version}}) and proposes ALLOW (approve), DENY (refuse) or ESCALATE (send to a person). A quotation gate keeps a legal statement only if its quoted words appear in the cited passage: this proves presence, not support for the conclusion. A ruling also needs a reviewer-approved applicability record (who and what the instrument covers). With no applicable passage, the system records what it searched and in which version, names the type of gap and asks a person. No legal advice, compliance or conformity with ITU-T Y.3172 is claimed. Tags: [offline] means no model was called; [measured] means a saved model run.

## 2. Description of the use case   [0.3 page]

Simulated service, real instruments: a federal Household Benefit Service (FHBS) wants AI to pre-check eligibility using household and employment records held by a Dubai Population Records Office (DPRO), collected for housing registration; a Complaints and Oversight Office receives a resident's complaint. A human officer makes every final decision. Roles only propose or request; none can decide or release records.

Why the UAE: the National Strategy for Artificial Intelligence 2031 aims to bring AI into government services, and the UAE Charter for the Development and Use of AI (2024) lists transparency and human oversight among its principles. An officer reviewing an AI-assisted step needs the quoted words behind it and the questions the texts leave open.

Gap in existing solutions: a search chat can answer fluently without showing which words guided it or saying "no passage settles this"; Gap Court adds a quotation gate, typed non-answers and a tamper-evident log.

## 3. Use case requirement   [0.35 page]

| # | Requirement |
|---|---|
| R1 | ALLOW or DENY requires a verified quotation from a binding instrument and reviewer-approved applicability for it; otherwise ESCALATE |
| R2 | A kept quotation appears in the cited passage (presence only) |
| R3 | A role sees only released steps addressed to it, never the hidden expected outcomes |
| R4 | A changed position is logged with the previous recommendation, the new fact and the reason. Saved runs: {{fact:total.revisions}} revisions after {{fact:total.contradicting_steps}} contradicting steps (S-04 demonstration alone: {{fact:demo.revisions}} after {{fact:demo.contradicting_steps}}) |
| R5 | Non-answers have types; a gap counts only when a named reviewer approves it as unresolved by the reviewed texts |
| R6 | A separate digest detects edits, deletion and reordering, not authorship or truth |

Each requirement maps to named offline tests in `docs/DETERMINISTIC_LAYER.md`. Cut-off, refused or unreadable answers are rejected for human review, never counted as non-answers. Offline suite: {{fact:tests.passed}} passing tests at code version {{fact:code.version}}.

## 4. Mapping with ITU-T Y.3172, the Readiness Framework 2.0 and domain policies   [0.8 pages]

The mapping is an analogy; fixed rules only route and reconcile. The `nomic-embed-text` run is not done; the numbers use the embedder {{fact:kb.embedder}}.

| Node (clause 8.1) | In Gap Court | Readiness 2.0 | Evidence |
|---|---|---|---|
| SRC, C | public texts, case facts, reviewer-approved applicability records; `kb-fetch` checks sha256 | 1 Data/Model Marketplace; 13 Digital Infrastructure | `corpus/APPLICABILITY.json`, `corpus/SOURCES.json` |
| PP | extract, mark, split; prepare search | 1 | `corpus/PREP.json`: {{fact:corpus.reproduced}} rebuild exactly, {{fact:corpus.audited}} match source words |
| M | trained model and search, disclosed | 8 Collaboration with AI | model id and settings fingerprint in every record |
| P | quotation check, applicability record, ALLOW/DENY gate, ESCALATE default | 10 AI & Policies | tests R1, R2 |
| D | send to roles with reason and passage id | 7 Strategy Alignment | records of each hand-off |
| SINK | gap list by type; analyst files | 10; 6 Human Interface | `docs/DOSSIER_S01.md` |

Requirements mapping to local policies (quotations the code checked and hashed, in `docs/DOSSIER_S01.md`, the case evidence file, unless marked):

| Requirement | Local provision |
|---|---|
| Purpose limits; federal holders of emirate data are covered | Dubai Law 26 of 2015, Article 3 and Article 15; Resolution 2 of 2017 (data policies), Article 1 and Article 3 |
| Consent for exchange between entities, stated purposes | Resolution 2 of 2017, Article 23 |
| Register data keeps the Law's protections | Executive Council Resolution 50 of 2024 (Unified Population Register), Article 4 and Article 8; Law 26 of 2015, Article 13 |
| Ask the customer once; share common data | UAE Smart Data Framework Part 1, Principle 3 (non-binding, context only); Digital Customer Policy, Article 5 (Arabic, checked transcription; legal rank not stated) |
| Information about and objection to automated decisions | federal personal data decree-law (official English text), Article 13 and Article 18; Article 2 excludes government entities (English quotation checked) |
| Complaint and grievance route | named only in draft evidence file D3; sources unchecked: Resolution 2 of 2017, Article 32 and Article 34; Law 24 of 2023 |

{{fact:dossier.claims_verified}} of the {{fact:dossier.claims}} quotations in the S-01 evidence file match passage text (presence, not a correct reading). The UAE AI Charter, Data Office decree and social support decree-law are not in the knowledge base.

## 5. Error scenarios   [0.8 pages]

Scenarios are author-made and SYNTHETIC; judges can add a step with `gapcourt add-step` and their own key. Roles: four model-backed roles (service agent, legal adviser, records custodian, oversight office) and a rule-based coordinator. Baseline: retrieval plus abstain (decline if nothing fits), no roles or verifier. The paired table counts agreement with approved labels, not correctness; ALLOW and DENY count as ANSWER.

| Step | Event | Expected ruling (approved label) |
|---|---|---|
| S-01 step 1 | may the pre-check use DPRO records? | ESCALATE to the oversight office |
| S-01 step 2 | FHBS asks DPRO for registry fields for a second purpose; DPRO collected them for housing only | DENY (refuse release); a human officer keeps authority |
| S-01 step 3 | a resident complains of a denial based on an inferred detail, with no notice of an automated pre-check | ESCALATE (is the review route adequate?) |
| S-02 | step 1: FHBS cites a federal once-only policy, DPRO Dubai exchange rules; step 2: does the federal data law's right to object cover the pre-check | step 1: ESCALATE (conflict unconfirmed); step 2: scope finding, the law does not apply (Article 2) |
| S-03 | household income threshold for eligibility | source coverage gap: the benefit instrument is missing |
| S-04 (development demonstration) | step 1: FHBS asks DPRO for one record, no consent; step 2: written consent is recorded | DENY, then ALLOW; reported separately |

Development sessions (not scored; the first two with unapproved labels): {{fact:dev.calls}} model requests, {{fact:dev.cost_usd}}, rejections {{fact:dev.rejections}}. Saved runs total: {{fact:total.cost_usd}} over {{fact:total.calls}} calls.

Scores (frozen labels, held-out run once, first result kept; single reviewer):

{{block:results}}

Every step of every S-01, S-02 and S-04 run was ruled ESCALATE, and no ruling was revised after a new fact. The matches above are steps whose approved ruling is ESCALATE; the DENY, ALLOW and scope rulings (S-01 step 2, S-04, S-02 step 2) were not reached. In one S-01 run the records custodian proposed DENY with checked quotations; the coordinator kept the legal adviser's ESCALATE. This is the conservative default of R1, not a success.

Retrieval check, offline, no model: an English-to-Arabic word map (an aid, not a translation) raised top-five hits on {{fact:retrieval.probes_n}} author-made questions from {{fact:retrieval.probes_before}} to {{fact:retrieval.probes_after}}, and left the development cases unchanged at {{fact:retrieval.dev_after}} of {{fact:retrieval.dev_scored}}; map and questions share an author and two concepts were adjusted after misses.

Pilot cases: {{fact:cases.draft_total}} ({{fact:cases.draft_dev}} development, {{fact:cases.draft_heldout}} held-out), approved and frozen by hash before scoring; blocked cases are not run.

{{block:evidence}}

## 6. Policy gaps and standards gaps derived from the mapping exercise   [0.8 pages]

Finding for S-01 step 2 (`docs/DOSSIER_S01.md`; checked quotations or marked as my reading): the Dubai Law and data policy limit use to the collection purpose, require consent for exchange between government entities and cover federal holders of emirate data; the federal Smart Data Framework (non-binding) and, on my reading of the Arabic, the Digital Customer Policy favour asking customers once. No reviewed text says how these combine when a federal service requests emirate register records for a second purpose: an ambiguity, not a missing rule, and a candidate gap until a reviewer rules. Elsewhere: GDPR Article 5 and Article 6 (purpose limits, compatible reuse); German SGB X Section 67c.

Gap findings for S-01 step 1 and step 3 (approved GAP labels; the system returned GAP on held-out cases PC18 and PC20). In the UAE instruments reviewed, none states a rule for (a) telling a resident that AI helped assess benefit eligibility, (b) explaining an AI-assisted benefit decision, or (c) human review of a government entity's AI-assisted pre-decision (development case PC19). Nearest rules: the federal personal data decree-law's right to object and to human review on request (Article 18), from which its Article 2 excludes government entities; the Dubai data policy's notice of which entity collects data (Resolution 2 of 2017, Article 23); the AI Charter's transparency and human oversight principles, which set no duties; DIFC Data Protection Regulation 10, notice of autonomous systems for DIFC entities only (from a law-firm summary, not the text).

Elsewhere (context only; EU texts read on secondary copies): (a) EU AI Act Article 26(11): deployers of high-risk AI that helps decide about people must inform them, and Annex III lists public assistance eligibility as high-risk; (b) EU AI Act Article 86, a right to an explanation of the AI system's role in a decision, and GDPR Article 15(1)(h), information about the logic of solely automated decisions; (c) GDPR Article 22 and UK GDPR Article 22C: human intervention, but only for decisions based solely on automated processing, so AI-assisted decisions with real human involvement fall outside.

Standards-gap note: node D (hand-off to roles) has no observed result for purpose limits across levels of government; no scored case tests it ({{fact:total.runs}} saved run folders, none testing this need). Possible need: each record travels with its collection purpose and consent status, and a rule says which rules govern federal requests for emirate records. The method is not tied to Dubai: the knowledge base is a per-jurisdiction manifest so the same checks could run on Abu Dhabi rules on top of the federal laws, or on Singapore's data protection law and AI governance framework (not tried).

Limitations: single reviewer; small source collection; author-made scenarios; simulated approvers; word-based search; the quotation and applicability checks test presence and approval, not correctness; an Arabic quotation supports a reading only, never a ruling; {{fact:corpus.audited}} of the source files are selected excerpts that match source words, not exact file rebuilds. References: `kb_release/REFERENCES.md` (KB {{fact:kb.version}}).
