# Key Design Trade-offs

> The submission asks for "architecture diagram + trust boundaries + key design trade-offs". The trust boundaries are in [`architecture.md`](architecture.md); this document is the **trade-offs** — each states what we chose, what we rejected, **what it costs**, and which tests pin it.
>
> This is not a complete ADR log (we do not have a 13-entry decision record); these are the **six decisions a reviewer should most check** — each corresponds to a real failure or a deliberate refusal, not an after-the-fact justification.

How to read it: the **cost** column is the point. A trade-off is only credible if its cost is written down — a decision with "no cost" is usually one that has not been thought through.

---

## D1 · The scorecard is computed by code; the model does not grade

| | |
|---|---|
| **Decision** | The Fit / Timing / Access / Intent scores in `gtm_research` are computed by `scorecard.py` from the existing rules in `sample_docs/gtm/icp-targeting.md` §6; the LLM is demoted to narrator |
| **Rejected** | Letting the LLM grade directly (more flexible; absorbs vague, colloquial customer descriptions) |
| **Why** | "Auditable" and "model-graded" conflict directly: the same customer profile scored 86 and 72 on two runs — sales cannot double-check it and compliance gets no evidence. Every point must trace to a concrete clause (e.g. "Series B+ → 20–25"), otherwise the score is just a well-phrased opinion |
| **Cost** | Signals outside the written clauses score nothing; input must contain structured facts (industry / headcount / funding round / monthly payment volume) — purely descriptive input lands on "collect facts" instead of an approximate score |
| **Evidence** | `test_gtm_scorecard.py`, 56 assertions: reproducibility 4 (same input, 5 runs, same score) · clause traceability 12 · **red line overrides score** 8 · **missing facts ≠ do-not-pursue** 8 |

> Group D exists on purpose: correctness-only tests would be passed by an implementation that judges every uncertain input "do-not-pursue" — that is caution disguised as safety. Missing facts must score 0 and be marked "needs human input", with a hard distinction between "complete facts, genuinely low score → a judgment" and "incomplete facts, low score → simply not asked yet".

---

## D2 · Retrieval defaults to lexical (BM25 + TF-IDF); dense embeddings are a switch, not the default

| | |
|---|---|
| **Decision** | Pure lexical retrieval by default; dense vectors behind a switch (`USE_DENSE_EMBED=1` + `EMBED_MODEL`, see `kb_qa.py` config) |
| **Rejected** | Vector retrieval by default (better synonym recall, friendlier to long-tail questions) |
| **Why** | ① **Reproducible**: CI and a judge's own machine need no embedding service or key — all ten suites, 337 assertions, pass in an empty environment; ② **Controllable**: permission filtering must happen at retrieval time (pre-retrieval ACL, OWASP LLM08), and the combination of a lexical index with ACL is far easier to pin with assertions; ③ **Corpus size**: 16 documents / 96 chunks — lexical is sufficient, and the mixed CN/EN tokenisation (single chars + bigrams) behaves predictably |
| **Cost** | Synonym rewrites rely on `QUERY_SYNONYMS` (query-side only; the corpus is untouched); proper nouns and function words in long queries depress the relevance score, more visibly on a small corpus |
| **Evidence** | The 0.17 gate calibrated on 27 labelled samples with three fully separated classes (irrelevant ≤0.145 / weak ≤0.152 / answerable ≥0.244); `test_relevance.py`, 21 assertions, guards the margin (irrelevant max 0.1492 < 0.17 < relevant min 0.2464) |

---

## D3 · Out-of-vocabulary words **must** count toward the BM25 ceiling

| | |
|---|---|
| **Decision** | `BM25.self_ceiling()` — when the query itself is scored as a document to compute the ceiling, words with df=0 still accumulate |
| **Rejected** | Accumulating only words that exist in the corpus (looks more "correct": why should absent words contribute to the ceiling?) |
| **Why** | **Measured counter-example**: on 10/15, changing it to accumulate only df>0 words made fully-OOV questions like "今天天气怎么样" jump from 0.15 to 0.73 — the denominator collapsed to a few common characters and the 0.17 threshold stopped working. In other words, "the query contains nothing the corpus knows" is exactly the strongest signal that this question should not be answered; the ceiling must include those words |
| **Cost** | Proper nouns and function words in long queries depress the score ("DIS-118 争议的处理结论是什么" scores below "争议处理结论"), more visibly on a small corpus |
| **Evidence** | The property is documented in a comment directly above `self_ceiling()` in `kb_qa.py` — **it was left by a real failure**, not written as design prose; the `test_relevance.py` calibration breaks immediately if it is "fixed" again |

> The production path is a cross-encoder rerank, not touching the ceiling itself. See "Known boundaries" in the README.

---

## D4 · The shield is rules and validation, not a model

| | |
|---|---|
| **Decision** | 11 identifier classes recognised by **validation**, not length guessing (cards pass Luhn, IBANs mod-97); prompt injection detected by phrase and structure rules |
| **Rejected** | Using an LLM (or small-model classifier) to judge "is this sensitive information / is this line an instruction" |
| **Why** | ① The entire point of this gate is to stop content **before it reaches an LLM** — the scanner itself cannot depend on another LLM call; ② rules can be pinned in CI by **paired assertions**; a model's judgment can only be argued with by "it feels right" |
| **Cost** | Injections rewritten to avoid the scan wordlists can bypass it; name detection covers annotated / titled names only, not street names. The remaining defences are "LLM has no write path + answer re-check + audit trail" |
| **Evidence** | `test_shield.py`, 32 assertions including **reverse assertions**: "a 16-digit serial failing Luhn must **not** be masked", "normal business text must **not** be deleted". A test that only checks "cards are masked" would be passed by an implementation that smears every number |

---

## D5 · Demo corpus + connector slots, not live platform integrations

| | |
|---|---|
| **Decision** | The corpus is 16 local Markdown files; every source-platform badge carries `·demo`; `/api/health` returns `demo_corpus: true`; connectors are slots behind a unified ingest contract + platform constant table |
| **Rejected** | Wiring real Confluence / Jira / Drive OAuth with per-query source-of-truth checks (another project in this track did exactly that) |
| **Why** | Concentrate limited verification effort on the **formal invariants of permissions and audit** — the things 337 assertions can re-prove in CI on every push; a connector implementation gets clicked once by a judge. Swapping in real connectors requires no change to any gate, test or invariant |
| **Cost** | No end-to-end verification against a real platform permission model — brief requirement #2 is therefore marked "**partial**" (the filtering mechanism is implemented and measured at leak=0, but not verified against a real platform) |
| **Evidence** | The README's "7 hard requirements → completion" table + [`requirements-map.md`](requirements-map.md); the "About the demo corpus" section states plainly that this is a **deliberate scope choice, not an unfinished feature in disguise** |

> Our stance on this one: better to write "partial" ourselves on the README's first screen than let a judge find it in the code. For reviewers from a licensed financial institution, stating clearly what is not done is itself part of Responsible AI.

---

## D6 · The audit trail lives in a SQLite hash chain, not an external log service

| | |
|---|---|
| **Decision** | SQLite WAL full audit + append-only triggers (blocking UPDATE/DELETE even from the owner connection) + per-row SHA-256 hash chain, re-verified in one click via `POST /api/audit/verify` |
| **Rejected** | Managed logging / WORM storage / external SIEM (closer to a production compliance setup) |
| **Why** | Zero external dependencies — a judge can click once, **right there**, and see "chain intact · N records all verifiable", instead of reading a statement that "we use immutable storage" |
| **Cost** | Single machine; someone with owner login could in theory rewrite the whole chain — so verify returns the head hash, which **must be recorded outside the system** (this limitation is written in the README, not hidden) |
| **Evidence** | `test_audit_chain.py`, 6 assertions: append-only triggers block UPDATE/DELETE + tampering is always located down to the row + legacy-db migration backfill |

---

## One principle running through all six

**Anything deterministic code can decide, the model does not get to.** Permissions, relevance, scoring, masking, audit — all in deterministic code. The LLM appears in exactly two places (sub-question planning, answer synthesis) and **has no write path anywhere**. The cost of this principle is flexibility (the cost columns of D1 and D2 both stem from it); the payoff is that every claim can be pinned by assertions: ten suites, 337 assertions, all green, with no LLM key required.

> Further reading: [`architecture.md`](architecture.md) (trust boundaries and the three gates' responsibilities) ·
> [`RED-TEAM-REPORT.md`](RED-TEAM-REPORT.md) (26 attacks, measured) ·
> [`requirements-map.md`](requirements-map.md) (brief clauses → mechanism → evidence)
