# What the code decides, what the model decides, what a person decides

Judges often ask "is this more than citation-checked RAG?". The short answer is a division of labour. This page states it so it can be checked against the tests. It describes behaviour of the code; it does not claim that any model run has happened (see `NOT_DONE.md`).

## The model proposes

A model-backed role (service agent, policy and legal adviser, records custodian, oversight office) reads the released step and the passages retrieved for its scope, and proposes: a decision word (ALLOW, DENY, ESCALATE, GAP, ABSTAIN), citations with quotations, applicability remarks, and a gap type. Nothing the model proposes is kept as-is.

## The code decides (no model involved)

| Rule | Where | Test that fails if broken |
|---|---|---|
| A citation is kept only if its passage was retrieved for this call and its collection is allowed for this role | `gate.py` `_check` | `test_uae_ruling_cannot_cite_global_collection`, `test_institution_scoped_to_own_documents` |
| A kept quotation must be found, word for word after whitespace, quote-mark and dash normalisation, in the cited passage (at least four words; fragments joined by an ellipsis must appear in order) | `verify.py` | `test_altered_word_fails`, `test_verified_means_only_presence_in_passage` |
| Instruction-like text in a quotation is refused as support (a narrow pattern list, not a guarantee) | `gate.py` | `test_instruction_like_quote_cannot_support_a_ruling` |
| ALLOW or DENY needs a verified quotation to a binding instrument; otherwise the ruling becomes ESCALATE | `gate.py` | `test_allow_without_verified_quote_is_downgraded`, `test_non_binding_only_citation_cannot_support_allow` |
| An unknown decision word becomes ABSTAIN | `gate.py` | `test_unknown_decision_becomes_abstain` |
| A scope finding ("instrument does not apply, per Article N") is kept only with a verified quotation | `gate.py` | `test_scope_finding_requires_verified_exclusion_quote` |
| A truncated, refused, empty or unparseable model answer is a visible rejection, never an abstention | `llm.py`, `roles.py` | `test_max_tokens_stop_is_visible_rejection_not_abstain` |
| Routing and reconciliation between roles follow fixed rules: conflicting firm assessments escalate; otherwise the adviser's assessment stands; a rejection counts as an escalation to a person | `roles.py` `Coordinator` | `test_coordinator_escalates_on_conflicting_assessments`, `test_coordinator_treats_rejection_like_escalate_for_other_roles` |
| A role sees only released steps addressed to it, and never the hidden labels | `journey.py`, `roles.py` | `test_role_sees_only_messages_addressed_to_it`, `test_future_steps_are_not_visible_before_release`, `test_hidden_labels_never_reach_prompts` |
| A model can never produce the type "candidate policy gap"; only a named reviewer promotes an entry | `gaps.py` | `test_system_cannot_emit_candidate_gap_without_reviewer` |
| Every step is written to a hash-chained log; alteration, deletion and reordering are detected, truncation only against the separately kept digest | `audit.py` | `test_altered_field_detected`, `test_deleted_middle_record_detected`, `test_reordered_records_detected`, `test_truncation_needs_the_separate_digest` |
| Held-out cases cannot be run or scored without a freeze record, and scoring refuses labels that differ from the frozen hash | `score.py`, `cli.py` | `test_run_cases_heldout_requires_freeze`, `test_score_refuses_heldout_when_labels_differ_from_the_freeze_record` |

## A person decides

- Whether a cited passage supports the conclusion or applies to the actor. The badge says "quotation verified", which means the words are present, nothing more.
- The final decision in the scenario (the roles propose and request; none issues a decision or releases records).
- Whether a typed gap is a "candidate policy gap" (named reviewer, coverage-check note).
- The case labels (single reviewer, stated as a limitation) and the freeze attestation.

## Why this matters for a judge

- The same input with a model that lies about a quotation produces a dropped citation and, for ALLOW or DENY, a downgrade to ESCALATE. That behaviour is testable offline, with a test double, without a key.
- The model's contribution is bounded: it can lose a citation or cause an escalation, it cannot widen a ruling beyond what the quoted binding text carries.
- What this does not show: that the quoted text is the right text, that retrieval found every relevant passage, or that the model reads Arabic. See `docs/BILINGUAL_HANDLING.md`.
