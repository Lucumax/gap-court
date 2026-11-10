#!/usr/bin/env python3
"""Generate dossiers/D1..D3 evidence skeletons and dossiers/search_records.json from the built KB.

Every quotation is checked against the KB text at generation time (the script fails if one is not found).
Conclusions are PRELIMINARY and agent-drafted; the reviewer field stays empty until the reviewer dispositions each dossier.
Comparators are 'agent-opened, not yet reviewer-verified' (the reviewer still checks them on the primary source).
Recorded searches use the dev-safe hybrid retriever; they are evidence of coverage checks, not scored runs.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gapcourt.chunking import ChunkParams  # noqa: E402
from gapcourt.embed import HashingEmbedder  # noqa: E402
from gapcourt.index import build_kb  # noqa: E402
from gapcourt.retrieve import HybridRetriever  # noqa: E402
from gapcourt.verify import check_quotation  # noqa: E402

OPENED = "2026-10-27"

DOSSIERS = [
    dict(
        id="D1", flags=['U12 Article 23 ("adhere to the following principles") does not show a notice duty for automated or AI-assisted decisions: Article 23(4)(a) is about informing people which entity will collect their data. It is listed above for completeness and is NOT treated as supporting the notice requirement.', 'U15 Specification DE6 is a redress mechanism, not notice, and the Smart Data Framework is non-binding in the knowledge base metadata; it cannot establish a duty.', 'Since the Session 6 retrieval path the recorded searches also return U01 Articles 13 and 18 (Arabic print edition). On the author\'s reading, Article 13 lists among the information a data subject may request the decisions taken on the basis of automated processing, including profiling, and Article 18 gives a right to object to such decisions. The skeleton\'s earlier wording "none found in the reviewed UAE instruments" was therefore too strong: the better reading is that the federal personal data law has such provisions but, by its Article 2(2), appears not to apply to government entities. This is a reading of Arabic text, not a verified quotation.',
      "The scope finding rests on the Arabic print edition of U01 only. It is the author's reading of the Arabic text, not a verified quotation, and its English wording has not been obtained.", 'Nothing in this skeleton is a supported policy finding. The conclusion "unresolved evidence" stands; the source-checked dossier for S-01 is docs/DOSSIER_S01.md (step 2).'], title="Notice and explanation for an AI-assisted benefit pre-decision",
        requirement=("A person who is the subject of an AI-assisted pre-decision about a public benefit is told that an "
                     "automated pre-check was used and can obtain an explanation of its role in the outcome."),
        queries=["notice that an automated system was used in a decision about a person",
                 "explanation of an automated decision to the person affected",
                 "inform the customer which entities used their documents and how"],
        existing=[("U12", "Article 23", "adhere to the following principles"),
                  ("U15", "Part: Specification DE6", "provide complaints and redress mechanisms for data subjects"),
                  ("U01", "Article 13", None), ("U01", "Article 18", None)],
        scope_finding=("U01 Article 18 gives a right to object to decisions of automated processing with legal or significant effect, "
                       "but Article 2(2)(a) and (b) exclude government data and government entities that control or process "
                       "personal data. On the Arabic print edition this reading is confirmed; the English wording is not yet obtained."),
        comparators=[
            ("G1", "Section 6", "Providing notice through all service delivery channels in use that the decision will be made or assisted by an automated decision system"),
            ("G1", "Section 6", "Providing a meaningful explanation to clients of how and why the decision was made"),
            ("G2", "Article 26", "shall inform the natural persons that they are subject to the use of the high-risk AI system"),
            ("G2", "Article 86", "clear and meaningful explanations of the role of the AI system in the decision-making procedure"),
            ("G3", "Article 13", "meaningful information about the logic involved"),
        ],
        comparability=("G1 is a federal executive directive scaled by impact level. G2 Annex III obligations apply from 2 December 2027 under "
                       "Regulation (EU) 2026/1744 (G2A); how Article 86 interacts with that deferral is unresolved. G3 Article 22 reaches decisions "
                       "'based solely on automated processing', so an officer-finalized decision may fall outside it. G7 has no general notice duty."),
        nodes="P (output checks), SINK (analyst workflow)", dims="10 AI and policies; 6 Human interface",
        chain=("requirement: notice and explanation; provision: none found that applies to a government entity making an AI-assisted decision (U01 Articles 13 and 18 address decisions of automated processing, but on the Arabic text Article 2(2) excludes government entities; reading, English text not obtained); node P/D; "
               "observed behavior: not yet run; result: not yet run; unresolved interface need (candidate, further review needed): a machine-readable "
               "marker that a decision record was AI-assisted, carried with the decision to the person-facing channel."),
        conclusion="unresolved evidence",
        why=("Three instruments that could carry the rule are not in the KB: the AI Charter (U04), the Data Office decree (U02) and the "
             "social-support decree-law (U10). Until they are added the result is a corpus coverage question, not a supported gap."),
        alternatives=["An instrument not in the KB already provides notice (U02, U04, U10): not excluded.",
                      "Retrieval missed a governing passage: the recorded searches above returned no AI-specific notice passage by title and locator; the reviewer confirms by reading.",
                      "The simulated entities fall outside the personal-data decree-law: yes on the Arabic text (Article 2(2)(b)); English wording pending."],
    ),
    dict(
        id="D2", flags=['Superseded for S-01 step 2 by docs/DOSSIER_S01.md, which checks every quotation and hash. Changes made there: U12 Article 17 is a classification criterion for Confidential data, not a purpose-limitation provision; the reference to U08 Articles 5.1 and 6.8.4 had no quotation (5.1 kept as an Arabic reading, 6.8.4 dropped); U13 Article 8 is cited for what it says, not as a purpose rule.'], title="Secondary use of records across the federal and emirate boundary",
        requirement=("Records that an emirate-level entity collected for one purpose are not used by a federal entity for a different "
                     "purpose without a stated legal basis and the person's knowledge."),
        queries=["records may be used only for the purpose for which they were collected",
                 "consent before exchanging personal data between government entities",
                 "federal government entity holding data relating to the emirate"],
        existing=[("U06", "Article 15", "Dubai Data may be used only for its intended purposes"),
                  ("U12", "Article 17", "disclosing Personal Data (excluding Sensitive Personal Data) for a purpose other than that for which it is collected"),
                  ("U12", "Article 23", "seek the consent of individuals and Private Entities to use, store, process, and exchange"),
                  ("U06", "Article 3", "Federal Government Entities which have any Data relating to the Emirate"),
                  ("U15", "Principle 3", "should only be requested to provide the same data to the government once"),
                  ("U13", "Article 8", "without prejudice to the rules and scope of legal protection of Personal Data")],
        scope_finding=None,
        comparators=[
            ("G3", "Article 5", "collected for specified, explicit and legitimate purposes and not further processed in a manner that is incompatible with those purposes"),
            ("G8", "Section 67c", None),
        ],
        comparability=("G3 applies to controllers in the EU (public authorities through the Article 4(7) definition of controller, not checked here); G8 is German social-data law whose Section 67c(3) allows "
                       "reuse for training AI models under conditions, read as text only (introduction date not checked). Neither transfers directly."),
        nodes="SRC, C (source registration), D (routing to institutional roles)", dims="7 Strategy alignment; 10 AI and policies",
        chain=("requirement: purpose limitation across levels of government; provision: U06 Article 15, U12 Articles 17 and 23 (emirate level), "
               "U08 Articles 5.1 and 6.8.4 and U15 Principle 3 (federal level); node D; observed behavior: not yet run; result: not yet run; "
               "unresolved interface need (candidate, further review needed): which regime controls when a federal entity pulls emirate records, "
               "and a way to carry the original collection purpose with each shared record."),
        conclusion="existing coverage found, with an unresolved question about which regime governs the federal recipient",
        why=("Purpose limitation and consent to exchange are stated at the emirate level and the federal policy level. U06 Article 3 names "
             "federal entities that hold emirate data, while U08 applies to federal entities. No reviewed text says how the two combine. "
             "This is an ambiguity (type 3), not an absence."),
        alternatives=["The rule is fully covered at one level and the other is redundant: possible; needs a legal reader.",
                      "A federal instrument outside the KB resolves the interaction (for example a Cabinet decision): not excluded."],
    ),
    dict(
        id="D3", flags=['U12 Article 34, U11 Article 6 and U06 Article 6 are grievance and complaint routes for data-policy decisions and data-provider compliance. They are listed as the nearest routes found and do NOT support a review route for an AI-assisted benefit decision.', 'U15 Specification DE6 and U16 Section 8 are non-binding in the knowledge base metadata and cannot establish a duty; U16 Section 8 concerns inaccurate data.', "The scope finding (U01 Article 18(4), Article 2(2)(b)) is the author's reading of the Arabic print edition, not a verified quotation.", 'G4 is a framework treaty that is not shown to be in force; its Article 14 line is context, not a standard.', 'Nothing in this skeleton is a supported policy finding. The conclusion "unresolved evidence" stands.'], title="Human review and challenge of an AI-assisted benefit decision",
        requirement=("A person can obtain a human review of, and challenge, an AI-assisted benefit decision, within stated time limits, "
                     "through a body that is not the deciding office."),
        queries=["right to obtain human intervention and contest an automated decision",
                 "grievance against a decision within working days committee",
                 "complaints and redress mechanism for the person affected"],
        existing=[("U12", "Article 34", "within fifteen (15) Working Days from the date of being notified of the contested decision"),
                  ("U11", "Article 6", "consider and determine the grievances submitted to the DDSE"),
                  ("U06", "Article 6", "investigate the complaints and violations related to Data Providers compliance"),
                  ("U15", "Part: Specification DE6", "provide complaints and redress mechanisms for data subjects"),
                  ("U16", "Section 8", "In case of inaccurate or incorrect data, the user can refer back to the entity")],
        scope_finding=("U01 Article 18(4) requires the controller to bring a human into review of automated decisions on request, but "
                       "Article 2(2)(b) excludes government entities (Arabic print edition; English wording not yet obtained)."),
        comparators=[
            ("G3", "Article 22", "at least the right to obtain human intervention on the part of the controller, to express his or her point of view and to contest the decision"),
            ("G7", "Section 4", "have access to a timely human review and a chance to appeal any negative impacts, when appropriate"),
            ("G1", "Section 6", "Informing clients of recourse options to challenge the administrative decision"),
            ("G1", "Part: Appendix C - Impact Level Requirements", "The final decision must be made by a human"),
            ("G4", "Article 14", "accessible and effective remedies for violations of human rights"),
        ],
        comparability=("G1 scales duties by impact level; G3 Article 22 is limited to solely automated decisions; G7 binds US federal agencies and "
                       "applies 'when appropriate'; G4 is a framework treaty that is not shown to be in force (see the comparator report). "
                       "G6 is voluntary guidance for private organizations."),
        nodes="P (approval policy), D (routing), SINK (gap register)", dims="8 Collaboration with AI; 10 AI and policies",
        chain=("requirement: review and challenge; provision: grievance routes in U12 Article 34 (data-policy decisions), U11 Article 6 and U06 Article 6 "
               "(complaints about data providers), none for a benefit decision; node P/D; observed behavior: not yet run; result: not yet run; "
               "unresolved interface need (candidate, further review needed): a hand-off record that lets a review body see the AI-assisted pre-decision "
               "inputs and the officer's final decision together."),
        conclusion="unresolved evidence",
        why=("The grievance routes found cover data-handling decisions, not an eligibility decision. The benefit instrument (U10) and the AI "
             "Charter (U04) are not in the KB, so absence of a benefit-decision review route cannot be concluded."),
        alternatives=["A review route exists in U10 or in executive regulations not in the KB: not excluded.",
                      "Existing grievance routes extend to benefit decisions by general administrative law: not tested."],
    ),
]


def find_text(kb, doc_id, locator):
    for e in kb.entries:
        c = e.chunk
        if c.doc_id == doc_id and c.locator == locator:
            yield e


def passage_ids(kb, doc_id, locator, quote):
    ids = []
    for e in find_text(kb, doc_id, locator):
        if quote is None or check_quotation(quote, e.chunk.text).verified:
            ids.append(e.chunk.chunk_id)
    return ids


def main() -> None:
    kb = build_kb([ROOT / "corpus", ROOT / "corpus_local"], HashingEmbedder(), ChunkParams())
    out_dir = ROOT / "dossiers"
    out_dir.mkdir(exist_ok=True)
    records = {"kb_version": kb.version, "retriever": "hybrid, k=5, collections kb_uae", "searches": {}}
    for d in DOSSIERS:
        retr = HybridRetriever(kb, ["kb_uae"], k=5)
        lines = [f"# {d['id']}: {d['title']}", "",
                 "Status: evidence skeleton drafted by an LLM from the KB. Conclusion is PRELIMINARY. Reviewer disposition: pending (Walter).",
                 f"KB version: {kb.version}. Date opened for every comparator: {OPENED}. Comparators are agent-opened, not yet Walter-verified.",
                 "This dossier states a domain-level requirement and compares global approaches. It makes no recommendation to any country.", "",
                 "## Flags from the source review (Session 6)"] + [f"- {x}" for x in d["flags"]] + ["",
                 "## Requirement at issue", d["requirement"], "", "## Existing provisions in the reviewed instruments"]
        for doc, loc, quote in d["existing"]:
            ids = passage_ids(kb, doc, loc, quote)
            if quote is not None and not ids:
                raise SystemExit(f"quote not found: {doc} {loc}: {quote}")
            if quote is None and not ids:
                lines.append(f"- {doc} {loc}: Arabic text; cited by locator only (no verified English quotation possible). Passage ids: " +
                             ", ".join(e.chunk.chunk_id for e in find_text(kb, doc, loc)))
            else:
                lines.append(f"- {doc} {loc} [{', '.join(ids)}]" + (f": \"{quote}\"" if quote else ""))
        if d["scope_finding"]:
            lines += ["", "## Scope finding", d["scope_finding"]]
        lines += ["", "## Search coverage (recorded searches, not scored)", f"Scope: kb_uae (8 documents). KB version {kb.version}."]
        recs = []
        for q in d["queries"]:
            hits = retr.search(q)
            recs.append({"query": q, "hits": [{"doc": h.chunk.doc_id, "locator": h.chunk.locator,
                                               "chunk_id": h.chunk.chunk_id, "score": round(h.score, 5)} for h in hits]})
            lines.append(f"- Query: {q!r}")
            lines.append("  Top hits: " + "; ".join(f"{h.chunk.doc_id} {h.chunk.locator}" for h in hits))
        records["searches"][d["id"]] = recs
        lines += ["", "## Alternative explanations and why they are not excluded"] + [f"- {a}" for a in d["alternatives"]]
        lines += ["", "## Global comparators (provision, quoted sentence, date opened, comparability)"]
        for doc, loc, quote in d["comparators"]:
            ids = passage_ids(kb, doc, loc, quote)
            if quote is not None and not ids:
                raise SystemExit(f"comparator quote not found: {doc} {loc}: {quote}")
            lines.append(f"- {doc} {loc}, opened {OPENED}" + (f": \"{quote}\"" if quote else ": text read, no quotation used here"))
        lines += ["", "Comparability and limits: " + d["comparability"], "",
                  "## Mapping tags", f"Y.3172 nodes (by analogy): {d['nodes']}. Readiness 2.0 dimensions: {d['dims']}.", "",
                  "## Standards-gap note (chain)", d["chain"], "",
                  "## Preliminary conclusion (agent-drafted)", f"{d['conclusion']}. {d['why']}", "",
                  "## Disposition", "Reviewer: (pending). Disposition: (pending). Date: (pending).", ""]
        (out_dir / f"{d['id']}.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    (out_dir / "search_records.json").write_text(json.dumps(records, indent=2), encoding="utf-8", newline="\n")
    print("wrote", len(DOSSIERS), "dossiers for", kb.version)


if __name__ == "__main__":
    main()
