# 16 — OPEX Data Collection Checklist

> **Who fills this in:** OPEX business owner / company staff, with build-team help.
> **Why it exists:** The verdict engine (§7) can only evidence "why we can apply" from an accurate company record. The v1.1 spec (§5.5) defers the KB template to OPEX-supplied content (O15) and leaves triage targets (O5/O6/O7) unresolved — this checklist is the mechanism for collecting that data.
> **How answers are used:** They resolve open decisions in `docs/13-open-decisions.md` and seed the structured OPEX Knowledge Base document (one structured markdown file, NOT a vector database — RAG is deferred, PROJECT_RULES #8).
> **Status field:** `DONE / PARTIAL / N/A` per item. Do not invent answers; mark unknown.

---

## 1. Company profile

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 1.1 | Legal name (exactly as registered) | | |
| 1.2 | Trading name, if different | | |
| 1.3 | Country/ies of registration & operation | | |
| 1.4 | Year established | | |
| 1.5 | Company registration / CAC number | | |
| 1.6 | VAT / taxpayer ID | | |
| 1.7 | Physical + registered address | | |
| 1.8 | Website | | |
| 1.9 | Contact person + email (for verification) | | |
| 1.10 | Staff count / size band | | |

## 2. Legal status & standing

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 2.1 | Entity type (private limited / etc.) | | |
| 2.2 | Director / shareholding details (as needed for tender eligibility) | | |
| 2.3 | Any debarment / sanction watchlists to check | | |

## 3. Core capabilities & services

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 3.1 | Core capabilities (what OPEX does) | | |
| 3.2 | Which of these are "evidence-backed" (past project proves it) | | |
| 3.3 | Which are aspirational / being built (no evidence yet) | | |

## 4. Past projects (evidence)

For every relevant project:

| # | Field |
|---|-------|
| 4.1 | Client name |
| 4.2 | Client country |
| 4.3 | Sector |
| 4.4 | Year(s) |
| 4.5 | Contract value |
| 4.6 | OPEX role (sole / lead / consortium member) |
| 4.7 | Scope / deliverables |
| 4.8 | Technologies used |
| 4.9 | Outcome |
| 4.10 | Reference / verifier (name + contact, OK to contact?) |
| 4.11 | Supporting document (CV, LPO, certificate) |

## 5. Certifications, accreditations, licences

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 5.1 | Certification / accreditation name | | |
| 5.2 | Certificate number | | |
| 5.3 | Issuing body | | |
| 5.4 | Issue date / expiry | | |
| 5.5 | Scope (what it covers) | | |
| 5.6 | Supporting document | | |

## 6. Key staff

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 6.1 | Name | | |
| 6.2 | Role | | |
| 6.3 | Education | | |
| 6.4 | Professional qualifications / certifications | | |
| 6.5 | Years of experience | | |
| 6.6 | Relevant sectors / countries | | |
| 6.7 | CV attached? | | |

## 7. Consortium / partners

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 7.1 | Partner name | | |
| 7.2 | Country | | |
| 7.3 | Capability they add | | |
| 7.4 | Relationship type / agreement | | |
| 7.5 | Their certifications (if they win as consortium) | | |
| 7.6 | Relevant sectors | | |

## 8. Known gaps

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 8.1 | Capabilities OPEX does NOT have (do not fabricate) | | |
| 8.2 | Certifications OPEX lacks that clients commonly ask for | | |

## 9. Triage: pursue / skip targets

These feed the Stage-A triage rules (§5.6) and resolve O5/O6/O7.

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 9.1 | Target sectors (pursue) | | |
| 9.2 | Sectors to skip / exclude | | |
| 9.3 | Target regions (pursue) | | |
| 9.4 | Regions to skip / exclude | | |
| 9.5 | Target contract types (e.g. framework, services, supply) | | |
| 9.6 | Minimum contract value (if any) | | |
| 9.7 | Target countries (if narrower than regions) | | |

## 10. Stakeholders & delivery (resolves O1-O3, O16)

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 10.1 | Tender recipients: who gets assessment emails, and does it differ by source/verdict/urgency? (O1) | | |
| 10.2 | Dev/test recipients: one or more seed addresses (O2) | | |
| 10.3 | Sender mailbox + authorisation for relay (e.g. Sendlib via Google OAuth2) (O3) | | |
| 10.4 | Should business staff get a subset of system alerts (operations list)? (O16) | | |

## 11. Strategy, budget & access (resolves O4, O8, O9, O12, O17)

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 11.1 | Monthly AI budget ceiling (O9) | | |
| 11.2 | Sendlib tier + provider chain positions 2 & 3 (O4) | | |
| 11.3 | Sending-domain / SPF-DKIM availability (O17) | | |
| 11.4 | TenderDetail subscribed access or public-only (O8) | | |
| 11.5 | Approval to send the real capability record to the chosen production LLM provider (O12) | | |

## 12. Attachments & email format (O20)

| # | Field | Answer | Status |
|---|-------|--------|--------|
| 12.1 | Sign-off on template additions: URGENT subject marker, notes footer, `possible_duplicate` state | | |
| 12.2 | Secure-link expiry default (proposed: 14 days) | | |

---

## Where each answer goes

| Checklist section | Lands in |
|---|---|
| 1–8 | OPEX Knowledge Base document (O15 template) → verdict engine evidence |
| 9 | Triage profile (Stage A rules, §5.6) |
| 10.1–10.2, 10.4 | `Recipient` seeding (tender list / dev alert list) |
| 10.3 | `MailProvider` sender config + OAuth authorisation |
| 11.1 | Budget guard configuration (§5.9.6) |
| 11.2–11.4 | Provider adapters to build (mail chain, TenderDetail) |
| 11.5 | `LLMProfile.approved_for_company_docs` on the production profile |
| 12.1–12.2 | Notification template (§8) confirmed format |

## Status discipline

- Items used for go-live (O1, O3, O4, O9, O11, O12, O16, O20 + triage profile) must be `DONE` before Test Mode is switched off (docs/13 blockers table).
- Unanswered items must never be silently invented in code (PROJECT_RULES #4, #5).