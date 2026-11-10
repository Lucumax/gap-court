#!/usr/bin/env python3
"""Write corpus/APPLICABILITY.json: one applicability record per instrument considered for journey J1.

Facts about scope are read from the instruments (tag V) by the agent that opened them on 2026-10-27; they are
drafts for the reviewer's approval. Legal reach questions are marked as unresolved, not decided.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

NOT_APPROVED = "agent-drafted 2026-10-27; not yet approved by Walter (W5)"

RECORDS = [
    dict(doc_id="U01", short="Federal Decree-Law 45 of 2021, personal data protection", in_kb=True,
         covers="Processing of personal data by controllers and processors in the State, and outside it where data subjects are in the State (Art. 2(1)).",
         exclusions="Art. 2(2): (a) government data; (b) government entities that control or process personal data; (c) personal data held by security and judicial authorities; (d) personal use; (e) health data with its own legislation; (f) banking and credit data with its own legislation; (g) free-zone entities with their own data protection legislation. Art. 1 'establishment' includes companies wholly or partly owned by federal or local government.",
         relevance_to_j1="Scope finding case: the simulated entities are government entities, so on this reading the instrument does not apply to them, and Art. 18 (objection to automated decisions with legal or significant effect; human review on request, Art. 18(4)) reaches covered controllers only.",
         language_note="Official Arabic print edition only was reachable; the Arabic text layer has ligature-order defects, so no verified English quotation is possible. The official English text sits on a host that blocks automated clients; Walter downloads it in a browser.",
         evidence="V (Arabic text, article and paragraph letters read by the author on 2026-10-27)", reach_status="reading confirmed on the Arabic text; English wording not obtained"),
    dict(doc_id="U06", short="Dubai Law 26 of 2015, data dissemination and exchange", in_kb=True,
         covers="Art. 3: Federal Government Entities which have any Data relating to the Emirate; Local Government Entities; persons designated by the Competent Entity.",
         exclusions="None stated in the text read.",
         relevance_to_j1="Names federal entities holding Dubai data; purpose-bound exchange (Art. 15: Dubai Data may be used only for its intended purposes); Art. 6(17) complaint route about data providers.",
         language_note="English official version; Arabic prevails in a conflict (footnote).", evidence="V",
         reach_status="whether an emirate-level law binds a federal recipient in practice is a legal question left unresolved"),
    dict(doc_id="U08", short="Federal Digital Customer and Digital Government Service Policy", in_kb=True,
         covers="All government services in federal government entities (Arts. 1 and 3 of the Arabic text).",
         exclusions="None stated.",
         relevance_to_j1="Once-only rule and consent to share documents between entities (Arts. 5.1, 6.8.4); no automated-decision or review rule.",
         language_note="Arabic only; not retrievable from English questions in the English-only build.", evidence="V (Arabic)",
         reach_status="legal rank of the policy is not stated; marked binding on its wording only"),
    dict(doc_id="U11", short="Dubai Law 24 of 2023, Dubai Data and Statistics Establishment", in_kb=True,
         covers="Establishes the DDSE; names Federal Government Entity and Other Entity categories (Art. 2).",
         exclusions="None stated.",
         relevance_to_j1="Regulator of the Dubai exchange law (Art. 6(5)); investigates complaints about data providers (Art. 6(17)); considers grievances (Art. 6(25)); AI applications in plans (Art. 6(2)).",
         language_note="English official version; Arabic prevails.", evidence="V", reach_status="as U06"),
    dict(doc_id="U12", short="Dubai Resolution 2 of 2017, Data Policies Document", in_kb=True,
         covers="Policies Document Art. 3(a): Federal Government Entities holding data relating to the Emirate; Local Government Entities; persons that produce, own, disseminate or exchange such data.",
         exclusions="None stated.",
         relevance_to_j1="Purpose limitation (Art. 17), consent to exchange between government entities (Art. 23), grievance with 15 and 30 working-day limits (Art. 34); decisions in scope are data-policy decisions, not benefit decisions.",
         language_note="English official version; Arabic prevails. Resolution Articles 1 to 3 and Policies Document Articles 1 to 3 share numbers; the second set carries the locator suffix [2].", evidence="V",
         reach_status="binding through Law 26/2015 on inference"),
    dict(doc_id="U13", short="Dubai Executive Council Resolution 50 of 2024, Unified Population Register", in_kb=True,
         covers="Art. 2: all Data Providers in the Emirate and the Dubai Population Data they hold.",
         exclusions="None stated.", relevance_to_j1="Emirate-level source of household records in the simulated journey (Art. 4: the only official source for Dubai Population Data).",
         language_note="English official version.", evidence="V", reach_status="Art. 8 keeps Law 26/2015 protections"),
    dict(doc_id="U15", short="UAE Smart Data Framework, Part 1 (v2.0, 2019)", in_kb=True,
         covers="Federal Government Entities, Local Government Entities, Semi-Government Entities and private entities exchanging data with government (1.3).",
         exclusions="Not stated.", relevance_to_j1="Principle 3 (give data once, with consent); DE6.2 (complaints and redress for data handling); DE7 (shared data access permissions).",
         language_note="English.", evidence="V", reach_status="non-binding framework with some mandatory specifications"),
    dict(doc_id="U16", short="Guide to Access Government Information (v1.0, 2017)", in_kb=True,
         covers="Basic principles for accessing federal government information (Section 1).", exclusions="Not stated.",
         relevance_to_j1="Individual access to and correction of own personal information (Section 8).",
         language_note="English.", evidence="V", reach_status="non-binding guideline"),
    # considered, not in the active KB
    dict(doc_id="U02", short="Federal Decree-Law 44 of 2021, UAE Data Office", in_kb=False, blocked="official host blocks automated clients (HTTP 403)",
         relevance_to_j1="Sets federal data policy and a data-complaints system (portal summary only).", evidence="S"),
    dict(doc_id="U03", short="UAE National Strategy for AI 2031", in_kb=False, blocked="official PDF host blocks automated clients; retired copy returns 404",
         relevance_to_j1="Top-level AI direction; strategy, not binding.", evidence="S"),
    dict(doc_id="U04", short="UAE Charter for the Development and Use of AI (2024)", in_kb=False, blocked="official PDF host blocks automated clients; portal summary only",
         relevance_to_j1="Twelve principles including human oversight and transparency; issue date differs across sources.", evidence="V for the portal summary, S otherwise"),
    dict(doc_id="U10", short="Federal Decree-Law 23 of 2024, Social Support and Empowerment", in_kb=False, blocked="official host blocks automated clients; portal summary only",
         relevance_to_j1="Eligibility and income-threshold rules for federal social support; the best federal benefit instrument.", evidence="V for the portal summary"),
    dict(doc_id="U05", short="UAE Digital Government Strategy 2025 (portal description)", in_kb=False, blocked="reference only; portal page, not a strategy document",
         relevance_to_j1="Data-driven and once-only principles.", evidence="V"),
    dict(doc_id="U14", short="Dubai Law 9 of 2022, Digital Services", in_kb=False, blocked="reference only; low bearing, no automated-decision or review provision",
         relevance_to_j1="Digital service data exchange cross-reference to Law 26/2015.", evidence="V"),
]


def main() -> None:
    sys_path = str(ROOT / "src")
    import sys

    sys.path.insert(0, sys_path)
    from gapcourt.corpus import load_document

    out = []
    for r in RECORDS:
        r = dict(r)
        p = ROOT / "corpus_local" / "uae" / f"{r['doc_id']}.md"
        if r["in_kb"] and p.exists():
            m = load_document(p).meta
            r.update(title=m["title"], issuer=m["issuer"], instrument_type=m["instrument_type"], binding=m["binding"],
                     language=m["language"], official_translation=m["official_translation"], source_url=m["source_url"],
                     retrieved_at=m["retrieved_at"], effective_date=m["effective_date"], status=m["status"],
                     terms_note=m["terms_note"], republish=m["republish"], source_sha256=m["sha256"])
        elif r["doc_id"] == "U01":
            pass
        r["approval"] = NOT_APPROVED
        out.append(r)
    path = ROOT / "corpus" / "APPLICABILITY.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(len(out), "records")


if __name__ == "__main__":
    main()
