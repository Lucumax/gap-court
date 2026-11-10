# Gap Court: the gaps it found

Gap Court is a decision aid for one simulated public-service journey. It searches a set of public UAE and international texts (its knowledge base), proposes ALLOW (approve), DENY (refuse) or ESCALATE (send to a person), and keeps a quotation only if those exact words appear in the passage it cites. When it finds no passage that settles a question, it says so and names the kind of gap. It does not guess.

On the eight held-out cases, scored once against labels I froze before the run, the full system agreed with the approved label in 7 of 8 cases and the retrieval-plus-abstain baseline in 2 of 8. This is a small pilot with author-made cases and a single reviewer, so it shows agreement on a pilot set, not legal correctness. In the scenario runs, every step of S-01, S-02 and S-04 was ruled ESCALATE, so the DENY, ALLOW and scope rulings the labels expect were not reached.

## The gap and abstain findings

GAP means that, in the UAE instruments reviewed, none states a rule that answers the question. ABSTAIN means the text that would answer it is not in the knowledge base, so the system declined to answer. Each case is an author-made question that I approved before the run.

| Case | The question, in plain words | What Gap Court decided | Where it looked | Trace |
|---|---|---|---|---|
| PC18 (held-out) | Must a resident be told that an AI system helped assess their benefit eligibility? | GAP. None of the instruments reviewed states such a rule. | Top five passages came from the UAE Smart Data Framework, Dubai Law 26 of 2015 and Dubai Resolution 2 of 2017. (Read from the held-out run log, which is withheld; see below.) | Case record: [cases.jsonl](https://github.com/Lucumax/gap-court/blob/main/runs/heldout/20261110T092416Z/cases.jsonl) (line for PC18). Full trace page: in the run log, available on request. |
| PC20 (held-out) | What explanation of an AI-assisted benefit decision must the agency give the person? | GAP. None of the instruments reviewed states such a rule. | Top five passages came from the same three instruments. | Case record: [cases.jsonl](https://github.com/Lucumax/gap-court/blob/main/runs/heldout/20261110T092416Z/cases.jsonl) (line for PC20). Full trace page: in the run log, available on request. |
| PC12 (held-out) | What does the national AI charter say about human oversight of AI systems? | ABSTAIN. The UAE AI Charter is not in the knowledge base. | Top five passages came from the UAE Smart Data Framework and the guide to the government information portal; none concerns the Charter. | Case record: [cases.jsonl](https://github.com/Lucumax/gap-court/blob/main/runs/heldout/20261110T092416Z/cases.jsonl) (line for PC12). Full trace page: in the run log, available on request. |
| PC14 (held-out) | What target does the national AI strategy set for AI use in government? | ABSTAIN. The UAE National Strategy for AI 2031 is not in the knowledge base. | Top five passages came from the same two instruments; none concerns the Strategy. | Case record: [cases.jsonl](https://github.com/Lucumax/gap-court/blob/main/runs/heldout/20261110T092416Z/cases.jsonl) (line for PC14). Full trace page: in the run log, available on request. |
| S-03 (scenario, run before the freeze) | What household income makes a family eligible for the benefit? | GAP (a missing source). The benefit instrument that sets the threshold is not in the knowledge base. | Dubai Unified Population Register resolution and the UAE Smart Data Framework. This run came before the freeze, on an earlier knowledge base version (kb-b08e7cfac2df). Its expected ruling was not fixed before the run. | [trace.html](https://github.com/Lucumax/gap-court/blob/main/runs/smoke/S-03-20261102T014545Z/trace.html) (download the file and open it in a browser) |

The nearest rules I found for PC18 and PC20 are the federal personal data decree-law's right to object and to human review on request (Article 18), from which its Article 2 excludes government entities; the Dubai data policy's notice of which entity collects data (Resolution 2 of 2017, Article 23); the AI Charter's transparency and human oversight principles, which set no duties; and DIFC Data Protection Regulation 10, which covers DIFC entities only and which I read from a law-firm summary, not the text. The search for PC18 and PC20 did not return the federal decree-law, so these rows are "no rule found by this search and my review", not proof that no rule exists.

The one held-out case that did not match was PC16, which asks whether the federal personal data law's right to object covers a government entity's eligibility pre-check. I labelled it a scope finding (the law does not apply, Article 2). The system sent it to a person instead.

Why the held-out traces are not public: in this run the model quoted 56 words of one link-only source (Dubai Resolution 2 of 2017, Article 34), more than the repository allows for a source we may only link to, not republish. The run's log and trace page are kept back, and [WITHHELD.md](https://github.com/Lucumax/gap-court/blob/main/runs/heldout/20261110T092416Z/WITHHELD.md) records their hashes. The case records, summary and fingerprint file (digest.json) are public.

## How to check a trace yourself

Clone the repository, run `python -m pip install -e ".[dev,prepare]"`, then `gapcourt verify-log --log runs/<run>/log.jsonl --digest runs/<run>/digest.json`. It confirms that no record in the log was changed, removed or reordered. It does not show who wrote a record or that it is true. For S-03 the run folder is `runs/smoke/S-03-20261102T014545Z`; open its `trace.html` in a browser to read each step, the passages searched and the quotations kept. Full results are in [results/heldout/results.md](https://github.com/Lucumax/gap-court/blob/main/results/heldout/results.md) and the cases and labels in [evals/labels_v1.jsonl](https://github.com/Lucumax/gap-court/blob/main/evals/labels_v1.jsonl).

## Limits

- Small pilot, and every finding holds only "in the instruments reviewed": author-made cases, a single reviewer, a small set of sources. The results show agreement with my labels, not legal correctness, and Gap Court gives no legal advice.
- A kept quotation shows that the words appear in the cited passage. It does not show that the passage supports the conclusion or applies to the person in question.
- U08 (the Arabic Digital Customer policy) is a checked transcription, not the official text layer. The EU texts were read on secondary copies and DIFC Regulation 10 from a law-firm summary. See [NOT_DONE.md](https://github.com/Lucumax/gap-court/blob/main/NOT_DONE.md) for the full list.

Independent entry for the AI for Good Lab Hackathon (United Arab Emirates), team Juncture Policy. No affiliation with or endorsement by ITU or any UAE authority. MIT licence.
