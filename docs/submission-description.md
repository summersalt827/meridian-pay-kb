# Submission copy (form-ready, Handbook §8 four-part structure)

> **Title**: Meridian Pay — Enterprise Knowledge Brain
> **Blurb** (≤10 words): *Ask anything. RBAC decides. You audit.*
> Paste the four paragraphs below into the Project Description field.

---

## Overview

Meridian Pay is an enterprise agent platform that answers questions across product,
compliance, and sales knowledge — with permissions enforced one level deeper than
the industry standard. Two workflows run on one parameterised skeleton: a knowledge
Q&A agent with cited, permission-checked answers, and a GTM account-intelligence
agent that turns a 2–3 hour customer-research task into a 30-second brief. The demo
is designed to be opened, not installed: **7 persona accounts, one shared password,
30 seconds from URL to the first answer** — no OAuth allowlists, no setup steps.
Two permission layers protect every answer: sub-question-level RBAC, and an audience
disclosure gate for customer-present conversations. Every number in this submission
is reproduced by CI on every push (7 suites, 139 assertions, all green).

## Real-world insights

Three observations from the Aspire brief and our own corpus drive the design.
First, *adoption*: building a workflow is the easy half — getting teams to use it is
the work, so the product ships with preset chips, role walkthroughs and a
zero-friction demo. Second, *permission semantics change in GTM*: inside the company
the question is "who may read this"; in front of a customer it becomes "what may be
said out loud" — a sales rep quoting an internal discount ceiling is an external
commitment, i.e. legal risk. No mainstream enterprise-RAG system models this
distinction (RAGFlow, Onyx, OpenGP all stop at document-level ACL). Third,
*composition leaks*: multi-hop agents merge sub-answers, and a combination can be
unauthorised even when every fragment passed its gate — document-level checks have
no place to catch this.

## Solution design

The engine plans a question into sub-queries, retrieves per sub-query, and merges.
RBAC is applied pre-retrieval inside the retriever (OWASP LLM08's recommended
control), so an out-of-scope sub-question retrieves nothing — a decomposed query
cannot fish out restricted documents. A second gate classifies every chunk as
`audience: external|internal` (held in code constants, never self-declared by
documents) and removes internal chunks in customer-present mode — measured by two
invariants: external-context zero leak, zero miss. A calibrated relevance gate
(0.17, tuned on 27 labelled samples) replaces "off-topic" guessing with a measured
decision. Real authentication (PBKDF2-HMAC tokens, per-identity rate limits), a
tamper-evident audit log (append-only triggers + SHA-256 hash chain, one-click
re-verification), and a bounded data-freshness window (resync API + mtime
citations). Honest boundary: the 16-document corpus is demo data — swapping the
Markdown ingestor for real Confluence/Jira connectors leaves every gate, test and
invariant unchanged (extractive fallback means the platform runs offline without
any LLM key).

## Business value

Quantified on the demo corpus and Aspire's own success metrics: customer research
drops from 2–3 hours to ~30 seconds per account; a case-study cycle dropped from 26
to 5 hours — directly supporting the challenge's 25%-SQL outbound goal. Compliance
posture: sub-question RBAC plus a verifiable audit chain maps to MAS Notice 626 /
FEAT accountability and PDPA least-privilege. Risk reduction for the revenue org:
the disclosure gate means nothing a sales rep says to a customer came from an
internal-only document — the story that gets the CRO's sign-off, not just IT's.
And trust is measurable, not asserted: 139 assertions across seven suites — leak
rate 0, recall loss 0, 26 adversarial attacks with zero leaks — all reproduced by
GitHub Actions on every push, clonable and re-runnable in five minutes.
