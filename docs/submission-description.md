# Submission copy (form-ready, Handbook §7 four-part structure)

> **Title**: Meridian Pay — Enterprise Knowledge Brain
> **Blurb** (≤10 words, 8 words): *Ask anything. RBAC decides. Shield masks. You audit.*
> (与 README、requirements-map 三处一致，勿再出现第二版本)
> **Demo video (5:57)**: https://youtu.be/b3Z1RCmMRmI (unlisted)
> **Live**: https://meridian-pay-kb.onrender.com · **GitHub**: https://github.com/summersalt827/meridian-pay-kb
> Paste the four paragraphs below into the Project Description field.

---

## Overview

Meridian Pay is an enterprise agent platform that answers questions across product,
compliance, and sales knowledge — with permissions enforced one level deeper than
the industry standard — and one layer deeper than that again, on the data itself.
Two workflows run on one parameterised skeleton: a knowledge Q&A agent with cited,
permission-checked answers, and a GTM account-intelligence agent that turns a 2–3 hour
customer-research task into a 30-second brief. The demo is designed to be opened, not
installed: **7 persona accounts, one shared password, 30 seconds from URL to the first
answer** — no OAuth allowlists, no setup steps. Three layers protect every answer:
sub-question-level RBAC, an audience disclosure gate for customer-present
conversations, and a need-to-know shield that masks identifiers *before* retrieved text
reaches the model. Every number in this submission is reproduced by CI on every push
(ten suites, 337 assertions, all green). **Built solo** — one developer, one AI
assistant, no teammates: 337 assertions, ten suites, one coherent codebase.

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
no place to catch this. Fourth, and the one most enterprise-RAG systems skip entirely:
*"may read" is not "needs to know"*. Being allowed to open a dispute log does not mean
you need the customer's card number, NRIC and home address in it — for a licensed
financial institution that gap is a PDPA purpose-limitation problem, and masking the
answer *after* the model has already seen the plaintext solves nothing. Fifth, borrowed
from OWASP LLM01: retrieved text is untrusted input. A document that says
"ignore all previous instructions" is not making a request the system should honour,
and a document that forges our own `[source N]` citation tag is attacking the context
boundary itself.

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
citations). A third gate — the shield — runs on the text itself before it reaches the
model: identifiers (card, NRIC/FIN, IBAN, account, phone, email, DOB, passport,
address, credentials) are masked by *checked* rules, Luhn for cards and mod-97 for
IBANs, so a 16-digit number that fails Luhn is left alone instead of being bluntly
mangled; instruction-like lines aimed at the model are stripped whole, forged citation
tags are neutralised, and zero-width characters removed. Our own `[source N]` prefix is
attached *after* the shield runs, so a document cannot forge the context boundary. The
answer is scanned again on the way out: an identifier the model was never shown and the
user never typed is masked even if the model invented it. The audit log stores the
question and answer fully masked — compliance evidence must not become a second leak
surface. Honest boundary: the 16-document corpus is demo data, and the platform badges
are a code-constant mapping rather than live connectors (labelled `·demo` in the UI and
`demo_corpus: true` in the API) — swapping the Markdown ingestor for real
Confluence/Jira connectors leaves every gate, test and invariant unchanged (extractive
fallback means the platform runs offline without any LLM key).

## Business value

Quantified on the demo corpus and Aspire's own success metrics: customer research
drops from 2–3 hours to ~30 seconds per account; a case-study cycle dropped from 26
to 5 hours — directly supporting the challenge's 25%-SQL outbound goal. Compliance
posture: sub-question RBAC plus a verifiable audit chain maps to MAS Notice 626 /
FEAT accountability and PDPA least-privilege. Risk reduction for the revenue org:
the disclosure gate means nothing a sales rep says to a customer came from an
internal-only document — the story that gets the CRO's sign-off, not just IT's. For
the compliance officer, the shield closes the gap that document-level ACL leaves open:
a permitted document no longer hands the model a customer's card number, and the audit
trail they reconstruct is itself masked, so producing evidence does not create new
exposure. And trust is measurable, not asserted: 337 assertions across ten suites —
leak rate 0, recall loss 0, 26 adversarial attacks with zero leaks, 32 shield checks
including the paired ones that prove precision (a number failing Luhn is *not* masked;
normal business text is *not* over-stripped) — all reproduced by GitHub Actions on
every push, clonable and re-runnable in five minutes.
