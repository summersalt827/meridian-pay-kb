# Why Sub-Question RBAC (and why document-level gates are not enough)

> **The category is being defined as document-level. That is exactly the failure mode.**
> Meridian Pay enforces permissions one stage earlier — at the sub-question level —
> and models a dimension the industry does not: *who is allowed to see the evidence at all*.

---

## 1. The failure mode: multi-hop composition

Multi-hop agents decompose a question into sub-questions, retrieve per sub-question,
then merge. Document-level access control evaluates each retrieved *document* against
the user's permissions. The problem: **each sub-question can pass the gate on its own,
while the composed answer crosses the line.**

Concrete example from our red team suite (D group, multi-hop puzzles):

| Sub-question (sales user) | Document-level verdict | Composed answer |
|---|---|---|
| "What's our discount approval rule?" | ✅ authorized (`gtm/pricing.md`) | The combined brief reveals both the *customer story* and the *discount ceiling a sales rep is not allowed to promise verbally*. Each fragment was authorized. The composition was not. |
| "Do we have a similar customer case?" | ✅ authorized (`gtm/case-studies.md`) | |

Document-level gates have no place to make this call — the violation exists only in
the *combination*, which is assembled after retrieval.

## 2. Where the industry actually is

The mainstream open-source enterprise-RAG ceiling is document-level:

- **RAGFlow** (~92k★): ACL filters at document granularity.
- **Onyx** (~32k★): 40+ connector ACL sync — still per-document.
- **OpenGP**: 96 Postgres RLS policies — row/document granularity.
- The newly coined category *"Permission-Aware AI Repository Gateway"* (Apr 2026)
  literally defines itself as enforcing **document-level permissions**.

None of them model *sub-question* granularity, because none of them do query
decomposition as a permission boundary.

## 3. What we do instead

**① RBAC at the sub-question level.** The planner decomposes the question; each
sub-question runs its own retrieval; the RBAC filter (`user.can_access`) is applied
per candidate chunk *inside* the retriever — pre-retrieval ACL (OWASP LLM08's
recommended control), not post-hoc filtering. An out-of-scope sub-question retrieves
nothing, so a decomposed query cannot fish out restricted documents. Measured, not
asserted: gold-standard suite, **leak rate = 0, recall loss = 0**; 26-check red team
suite (semantic rewrites, prompt injection, path escape, role forgery, multi-hop
puzzles, threshold suppression) — **0 leaks**.

**② The audience gate — a second permission dimension nobody models.**
In GTM, permissions change meaning: the question is no longer *"who may read this"*
but *"may this chunk be said to an external person"*. A sales rep who can read
`gtm/pricing.md` internally must not have its discount ceilings flow into a
customer-present answer — saying the wrong thing to a customer is an external
commitment, i.e. legal risk. Our `audience: external|internal` classification
(held in code constants, never self-declared by documents — `pricing.md` literally
says "external quotes follow the quotation sheet" and is still classified internal,
a deliberate trap test) is enforced as a second per-candidate gate
(`user.can_disclose`), so it inherits sub-question granularity for free.

Demo, one toggle: the same sales account asks
*"客户问我们有没有同类案例，以及能给多少折扣"* —
internal context returns **4 documents** (case studies, pricing, objection playbook,
sales SOP); customer-present mode returns **exactly one** — `gtm/case-studies.md`,
the only chunk classified external. Verified by two invariants: external-context
**zero leak** and **zero miss** (the gate narrows; it never silently empties).

## 4. Prior art — and where we draw the line

`RZ-Logic/regulated-rag` argues *"citation grounding and refusal belong in code,
not in prompts"* — the same school as our **"by measurement, not by construction."**
We make the same argument one stage earlier: it verifies citations **post-generation**;
we filter evidence **pre-retrieval** — and add the dimension it does not model:
*who is allowed to see the evidence at all*, and *in what context*.

## 5. Measured, not promised

Every claim in this document is reproduced by CI on every push — seven suites,
**227 assertions** (RBAC gold standard, relevance calibration on 27 labelled samples,
auth attack surface, 44-case web matrix, 26-check red team, audit hash chain,
disclosure gate), all green on GitHub Actions (2026-10-14).

*Attack it, don't assert it.* — our red team report lives in [RED-TEAM-REPORT.md](RED-TEAM-REPORT.md).
