#!/usr/bin/env python3
"""Write evals/pilot_cases_DRAFT.jsonl: 20 candidate pilot cases drafted by an LLM from the corpus.

Every row is status DRAFT_UNAPPROVED. The author is the single reviewer; nothing here is a label until the reviewer edits
and approves it and the file is hashed. Split: 10 dev, 10 held-out. Held-out cases must not be used for
tuning or scored before the freeze.
"""
import json
from pathlib import Path

D = "DRAFT_UNAPPROVED"
NOTE_LLM = "LLM-drafted candidate; unapproved; single reviewer pending"

CASES = [
    # answerable (6)
    dict(case_id="PC01", split="dev", category="answerable",
         question="May Dubai data be used for a purpose other than the one it was collected for?",
         expected=[("U06", "Article 15")], expected_outcome="ANSWER",
         basis_quote="Dubai Data may be used only for its intended purposes"),
    dict(case_id="PC02", split="heldout", category="answerable",
         question="Within how many working days must an affected party file a grievance against a decision under the Dubai data policies?",
         expected=[("U12", "Article 34")], expected_outcome="ANSWER",
         basis_quote="within fifteen (15) Working Days from the date of being notified"),
    dict(case_id="PC03", split="dev", category="answerable",
         question="Which Dubai body manages the Unified Population Register?",
         expected=[("U13", "Article 4")], expected_outcome="ANSWER",
         basis_quote="The DDSE will manage and control the Register"),
    dict(case_id="PC04", split="heldout", category="answerable",
         question="How often should a customer of a government entity have to give the same data to the government?",
         expected=[("U15", "Principle 3")], expected_outcome="ANSWER",
         basis_quote="should only be requested to provide the same data to the government once"),
    dict(case_id="PC05", split="dev", category="answerable",
         question="What must a government entity do before it exchanges a person's personal data with other government entities in Dubai?",
         expected=[("U12", "Article 23")], expected_outcome="ANSWER",
         basis_quote="seek the consent of individuals and Private Entities to use, store, process, and exchange"),
    dict(case_id="PC06", split="heldout", category="answerable",
         question="Does the Dubai data exchange law reach federal government entities that hold data about the emirate?",
         expected=[("U06", "Article 3")], expected_outcome="ANSWER",
         basis_quote="Federal Government Entities which have any Data relating to the Emirate"),
    # paraphrase retrieval traps (4): wording differs from the passage
    dict(case_id="PC07", split="dev", category="paraphrase_trap",
         question="Can a ministry repurpose residents' registry details for a new program without asking them?",
         expected=[("U06", "Article 15"), ("U12", "Article 17")], expected_outcome="ANSWER",
         basis_quote="Dubai Data may be used only for its intended purposes",
         note="any-of: either expected passage counts"),
    dict(case_id="PC08", split="heldout", category="paraphrase_trap",
         question="Who looks into complaints about agencies mishandling shared records in Dubai?",
         expected=[("U06", "Article 6"), ("U11", "Article 6")], expected_outcome="ANSWER",
         basis_quote="investigate the complaints and violations related to Data Providers compliance",
         note="any-of; basis quote is from U06 Article 6; U11 Article 6 has the parallel power"),
    dict(case_id="PC09", split="dev", category="paraphrase_trap",
         question="Where can a resident who thinks the government holds wrong details about them get them fixed?",
         expected=[("U16", "Section 8")], expected_outcome="ANSWER",
         basis_quote="The user must update or correct his personal information"),
    dict(case_id="PC10", split="heldout", category="paraphrase_trap",
         question="What privacy principles must agencies publish together with a way for people to complain?",
         expected=[("U15", "Part: Specification DE6")], expected_outcome="ANSWER",
         basis_quote="provide complaints and redress mechanisms for data subjects"),
    # deliberately missing documents (4): the governing instrument is NOT in the KB
    dict(case_id="PC11", split="dev", category="missing_document",
         question="What household income threshold decides eligibility for federal social support?",
         expected=[], expected_outcome="ABSTAIN", missing_source="U10 Federal Decree-Law 23 of 2024 (blocked to automated download)"),
    dict(case_id="PC12", split="heldout", category="missing_document",
         question="What does the national AI charter say about human oversight of AI systems?",
         expected=[], expected_outcome="ABSTAIN", missing_source="U04 UAE Charter for the Development and Use of AI (blocked to automated download)"),
    dict(case_id="PC13", split="dev", category="missing_document",
         question="What complaints system does the federal data office run for people whose data is mishandled?",
         expected=[], expected_outcome="ABSTAIN", missing_source="U02 Federal Decree-Law 44 of 2021 (blocked to automated download)"),
    dict(case_id="PC14", split="heldout", category="missing_document",
         question="What target does the national AI strategy set for AI use in government?",
         expected=[], expected_outcome="ABSTAIN", missing_source="U03 National Strategy for AI 2031 (blocked to automated download)"),
    # ambiguity, conflict, or unambiguous exclusion (3)
    dict(case_id="PC15", split="dev", category="ambiguity",
         question="Which consent rule governs a federal service that pulls a resident's records from a Dubai entity: the federal policy or the Dubai policy?",
         expected=[("U12", "Article 23"), ("U15", "Part: Specification DE6")], expected_outcome="ESCALATE",
         basis_quote="seek the consent of individuals and Private Entities to use, store, process, and exchange",
         note="correct output is 'sources disagree or are unclear for this actor' and escalation; federal U08 Article 6 (Arabic) is the other side but is not retrievable from English queries"),
    dict(case_id="PC16", split="heldout", category="ambiguity",
         question="Does the federal personal data law's right to object to automated decisions apply to a government entity's eligibility pre-check?",
         expected=[("U01", "Article 2")], expected_outcome="SCOPE_FINDING",
         scope_statement="instrument does not apply, per Article 2",
         blocked_on="English text of U01 (the Arabic text layer cannot support a verified English quotation); Walter to download the official English text in a browser (W5)",
         note="unambiguous exclusion, not a conflict: Article 2(2)(a) government data and 2(2)(b) government entities that control or process personal data"),
    dict(case_id="PC17", split="dev", category="ambiguity",
         question="May a federal benefit service pull records from the Dubai Unified Population Register, given the register's rules and the federal once-only policy?",
         expected=[("U13", "Article 5"), ("U06", "Article 9")], expected_outcome="ESCALATE",
         basis_quote="exchange their Shared Data, in accordance with the rules and conditions adopted by the Competent Entity",
         note="basis quote is from U06 Article 9; the register Article 5 sets who must provide data; reach over a federal recipient is unresolved"),
    # candidate-gap cases (3): bounded finding expected; analyst decides
    dict(case_id="PC18", split="heldout", category="candidate_gap",
         question="Must a resident be told that an AI system helped assess their benefit eligibility?",
         expected=[], expected_outcome="GAP", comparators=["G1 6.2.1", "G2 Art 26(11)", "G3 Art 13(2)(f)"],
         note="notice requirement; reviewed instruments held no AI-specific notice passage in the first read; analyst confirms or rejects"),
    dict(case_id="PC19", split="dev", category="candidate_gap",
         question="Is a resident entitled to a human review of an AI-assisted eligibility pre-decision?",
         expected=[], expected_outcome="GAP", comparators=["G3 Art 22(3)", "G7 4(b)(vi)", "G1 Appendix C"],
         note="human review; U12 Article 34 and U15 DE6 give data-handling redress, not decision review"),
    dict(case_id="PC20", split="heldout", category="candidate_gap",
         question="What explanation of an AI-assisted benefit decision must the agency give the person?",
         expected=[], expected_outcome="GAP", comparators=["G2 Art 86(1)", "G1 6.2.3"],
         note="explanation; no reviewed passage found in the first read; analyst confirms or rejects"),
    # adversarial applicability case (added 2026-11-05, development only): the tempting quotation belongs to another actor
    dict(case_id="PC21", split="dev", category="applicability_trap",
         question="Must a private shop in Dubai put its customer data on the government's Electronic Platform?",
         expected=[("U12", "Article 3 [2]"), ("U12", "Article 9")], expected_outcome="ESCALATE",
         basis_quote="and who are determined by the DDE",
         note="adversarial: Article 9(a), 'all Government Entities must disseminate Open Data and exchange Shared Data through the Electronic Platform', "
              "is an authentic quotation about another actor; Article 3(a)(3) reaches private persons only if the DDE determines them, and no "
              "determination is in the knowledge base. A decisive answer resting on Article 9 fails the applicability test. GAP is the competing reading; reviewer decides"),
]


def row(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k != "expected"}
    out["expected"] = [{"doc_id": d, "locator": loc} for d, loc in c["expected"]]
    out["status"] = D
    out["drafted_by"] = NOTE_LLM
    return out


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    (root / "evals").mkdir(exist_ok=True)
    lines = [json.dumps(row(c), ensure_ascii=False, sort_keys=True) for c in CASES]
    (root / "evals" / "pilot_cases_DRAFT.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(len(lines), "cases written")
