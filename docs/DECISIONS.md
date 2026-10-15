# Key Design Trade-offs

Five decisions a reviewer is likely to question. Each states the alternative we rejected and why.

## 1. Sub-question RBAC after scoring, not ACL-prefixed retrieval

**Decision.** A decomposed query retrieves each sub-question independently against a directory-scoped corpus, then filters by the absolute relevance gate. We do not push ACL predicates into the retrieval query itself (the VaultRAG pattern).

**Alternative rejected.** ACL-prefixed retrieval guarantees out-of-scope chunks never enter the pipeline, but it silently degrades: asking the wrong question returns visible-but-irrelevant results, indistinguishable from "you lack permission".

**Why ours.** Our design can tell the two apart: out-of-scope content yields a clean `no_context` rejection, never a confident fabrication from keyword collisions. The gold-standard suite asserts both directions — `leak rate = 0` **and** `recall loss = 0` (8/8), so the filter is proven not to over-block.

## 2. Lexical hybrid retrieval with a calibrated gate, not embeddings-first

**Decision.** BM25 + TF-IDF over mixed CN/EN tokenisation, ranked by an absolute relevance score (`BM25 / query self-match ceiling`) with a 0.17 threshold calibrated on 27 labelled samples (irrelevant ≤0.1492, relevant ≥0.2464 — 1.14x / 1.45x margin).

**Alternative rejected.** Dense embeddings as the default retriever. A `DenseEmbedder` exists behind `USE_DENSE_EMBED=1`, but embeddings rescore the whole ranking and would force re-calibrating the gate — the gate's separation is the moat; swapping the ranker risks it for an unmeasured gain on a 16-document corpus.

**Known boundary (stated honestly).** Single-keyword queries sit near the lexical ceiling; the production path is a cross-encoder rerank, listed as future work rather than hidden.

## 3. Deterministic scoring and drafting; the LLM narrates, it does not grade

**Decision.** The GTM scorecard (`scorecard.py`) and first-touch email draft (`outreach.py`) are computed by code from the rules already written in `gtm/icp-targeting.md` §6. The LLM receives the scores as established facts and is instructed not to alter them.

**Alternative rejected.** Letting the model grade and draft freely. Two runs would then produce different scores with no traceable provenance per point — a direct conflict with the auditability claim the whole product rests on, and a compliance problem in a fintech sales context.

**Evidence.** 56 + 105 assertions pin reproducibility (same input → identical score, tier and rendered text across 5 runs) and traceability (every number in the email maps to a template constant or a user-supplied fact). The red-line list takes precedence over any score; a sanctioned entity gets no outreach copy at all.

## 4. Demo corpus with reserved connector slots, labelled honestly

**Decision.** The demo ingests 16 local Markdown documents under a `MarkdownIngestor` connector; source badges map to Confluence / Drive / Jira as **reserved slots**, and the API reports `demo_corpus: true` with `·demo` badges in the UI.

**Alternative rejected.** Shipping unverified live connectors in the submission window. Four real OAuth integrations cannot be built and hardened in the remaining time; a half-working connector is worse than an honest slot, because a judge will discover it in five minutes.

**Why the label is a feature.** The gates, shield and audit invariants are connector-agnostic: swapping the ingestor does not touch RBAC, the relevance gate, or the hash chain. The scope choice is stated in the README rather than discoverable — for an Aspire reviewer, a self-declared boundary reads as engineering honesty, and the `·demo` badge prevents the demo corpus from being mistaken for a live integration.

## 5. Shield between retrieval and the LLM; masking is need-to-know, not access control

**Decision.** PII masking and prompt-injection line-removal (`shield.py`) run on the assembled context **after** RBAC and the relevance gate, **before** the LLM sees it. Answers are re-scanned after generation. The audit trail stores only rules and counts, never plaintext or masked values.

**Alternative rejected.** Treating masking as an extension of RBAC (pre-retrieval), or relying on prompt-level instructions to the model.

**Why ours.** Need-to-know is orthogonal to access: a user permitted to read a document still does not need every card number inside it. Luhn / mod-97 validation keeps the mask precise — a 16-digit number that fails Luhn is not masked, business text is never deleted (both are asserted by paired reverse tests, so a "mask everything" implementation cannot pass). Source prefixes are appended **after** the shield, so documents cannot forge context boundaries. The `shield_stats` audit column is deliberately excluded from the hash-chain canonical, so the migration added the column without invalidating 708 existing chained rows.
