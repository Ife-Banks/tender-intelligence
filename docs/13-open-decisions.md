# 13 — Open Decisions

> **This document is the contract for "do not invent."** Every item below is a business or technical decision the source documents do **not** settle. The coding AI must not choose on behalf of the business; when an open decision is required for implementation, STOP and request it (PROJECT_RULES #5, #19).
>
> Sources: v1.1 §12.3 (resolved/deferred/open tables), §0.2 (decision log), v1.0 open questions, plus items discovered while reading.
> Status references: **OPEN** (must be decided before the affected feature ships), **[PROPOSED]** (a suggested default exists but is unconfirmed).

---

## O1. Actual tender recipients

```
Decision: Who are the tender recipients, and does the list differ by source, verdict, or urgency?
Why it matters: §5.10.1 recipient list powers every notification; Test Mode only delays this,
it does not answer it.
Current status: OPEN (v1.1 §12.3 #1).
Known options: (a) all-sources all-verdicts list; (b) per-source `recipient_scope`; (c) receives_filter
by verdict (all / APPLY+CONDITIONS / urgent); (d) any combination of (a)+(b)+(c).
Information required: Named recipients, delivery (To/CC/BCC), and routing filters.
Who should decide: OPEX business owner.
Impact if unresolved: Notification routing cannot be finalised; go-live blocked.
```

## O2. Developer / test recipients (dev alert list)

```
Decision: Who is on the dev alert list?
Why it matters: Alerts are the anti-silent-failure mechanism (§5.10.4); v1.1 only guarantees
"seeded from an environment variable" and that the last active dev recipient can't be removed.
Current status: Information needed for production seed value (env var + admin entries).
Known options: TBD by the building team/owner.
Information required: One or more dev email addresses to seed; who maintains the list.
Who should decide: The development lead/maintainer (with owner sign-off).
Impact if unresolved: Alerts cannot reach anyone until a value is chosen; delivery of the
"fail loudly" goal is at risk.
```

## O3. Sender mailbox

```
Decision: Which mailbox is connected as the sender, and is OPEX comfortable authorising
Sendlib to relay through it (Google OAuth2)?
Why it matters: Emails will come from this address (§5.10.3); authorising a third-party relay
is a security/trust decision; a dedicated mailbox is advised (not a personal inbox).
Current status: OPEN (v1.1 §12.3 #3).
Known options: OPEX Google Workspace account on OPEX's own domain (gives professional From with
no extra DNS); a dedicated Gmail mailbox; other.
Information required: Mailbox address, owner/authorisor, willingness to authorise Sendlib.
Who should decide: OPEX (business + IT), with an explicit security-noted decision.
Impact if unresolved: Sendlib adapter cannot be enabled for production; phase-1 Test Mode sends
still blocked by lack of an authorised OAuth connection.
```

## O4. Mail plan / provider chain (Sendlib tier + providers 2 & 3)

```
Decision: Sendlib Free or Pro, and which providers fill positions 2 and 3 in the chain?
Why it matters: Free tier (5 attachments, 1 MB each) forces most documents to links; Pro
(20 × 10 MB) meets "every document attached" more often; providers 2/3 guarantee failover.
Current status: OPEN (v1.1 §12.3 #4, #14).
Known options: Free vs Pro; conventional transactional API or SMTP for positions 2/3; decision
depends partly on whether a sending domain will be available (SPF/DKIM, §12.2).
Information required: Tier budget; domain availability; preferred providers.
Who should decide: OPEX (budget) + build/maintain team (provider selection).
Impact if unresolved: Attachment planner behaviour in production unknown; provider 2/3 adapter
cannot be built; chain-failover acceptance (v1.1 §11) cannot be met.
```

## O5. Target sectors

```
Decision: Which sectors should triage target or skip?
Why it matters: Stage A triage rules need sector targets (§5.6); defaults come from the KB's
pursue/skip section; the actual profile is explicitly open (v1.1 §12.3 #5).
Current status: OPEN.
Known options: Derive default from KB sections; or explicit sector list.
Information required: OPEX's pursuit/skip sectors.
Who should decide: OPEX business owner.
Impact if unresolved: Triage cannot be correctly configured; irrelevant tenders may flood the
verdict stage or relevant ones be dropped.
```

## O6. Target regions

```
Decision: Which regions should triage target or skip?
Why it matters: Triage rules include regions (v1.1 §5.6, §5.8).
Current status: OPEN.
Known options: Regional scope (West Africa, Africa-wide, other); skip/blocklist.
Information required: OPEX's regional strategy.
Who should decide: OPEX business owner.
Impact if unresolved: Triage misrouting; wrong tenders to Stage B.
```

## O7. Minimum contract value

```
Decision: Is there an optional minimum contract value for triage?
Why it matters: v1.1 §5.6 lists it as an optional triage rule; the value is not specified anywhere.
Current status: OPEN (not settled in v1.1).
Known options: None set / threshold(s).
Information required: OPEX's minimum-value policy per sector if any.
Who should decide: OPEX business owner.
Impact if unresolved: Cannot apply a value filter; high-volume sources will pass more to Stage B.
```

## O8. TenderDetail access

```
Decision: Does OPEX have/want a TenderDetail subscription account, or is the source scraped from
public free-tier pages only?
Why it matters: TenderDetail appears subscription-based; affects whether the Phase-3 adapter
uses paid access (v1.1 §12.3 #6).
Current status: OPEN.
Known options: OPEX account (then per-site auth flows), public-only scraping, or drop the source.
Information required: OPEX's access arrangement.
Who should decide: OPEX.
Impact if unresolved: Phase-3 TenderDetail adapter scope unknown.
```

## O9. Monthly AI budget

```
Decision: What is the acceptable AI cost budget per month?
Why it matters: Budget guard alerts at 80% and pauses Stage B at 100% (§5.9.6); several listed
sources publish very high volumes (v1.1 §12.3 #7).
Current status: OPEN.
Known options: A currency amount; possibly per-provider.
Information required: OPEX-approved monthly figure; whether emergency overage is acceptable.
Who should decide: OPEX.
Impact if unresolved: Budget guard cannot be configured; cost risk unmanaged.
```

## O10. Storage choice

```
Decision: Where should the knowledge base and document archive physically live?
Why it matters: Cloud storage the company already uses vs something new; object storage or
filesystem acceptable per §9 (v1.1 §12.3 #8).
Current status: OPEN.
Known options: Existing company cloud account(s); new bucket/store; on-prem filesystem.
Information required: OPEX's storage preference and available accounts.
Who should decide: OPEX (IT).
Impact if unresolved: Document archive + KB storage implementation cannot be finalised; signed
link serving depends on it.
```

## O11. Admin users

```
Decision: Who gets admin-app access, and with which role (Admin/Viewer)?
Why it matters: §5.11 auth is confirmed; the exact login method and user list are open
(v1.1 §12.3 #9).
Current status: OPEN.
Known options: Admin/Viewer roles [PROPOSED]; identity via internal accounts or company SSO.
Information required: Named users + roles; preferred login method.
Who should decide: OPEX.
Impact if unresolved: Admin app cannot be secured/deployed to named users; viewer access to
KB/secrets must remain blocked regardless.
```

## O12. Production LLM provider & data-handling approval

```
Decision: May OPEX's real capability record be sent to the chosen production LLM provider?
Why it matters: Verdict calls send company KB to the provider; the approved_for_company_docs
flag on the production profile requires explicit business approval (v1.1 §5.9.4, §12.3 #10).
Current status: OPEN; production model name also DEFERRED (v1.1 §12.2 — believed GPT-4-class "mini",
exact id TBD).
Known options: Approve the production provider; or keep it unapproved and stay on placeholder KB.
Information required: OPEX data-handling sign-off; exact model id/credentials for the production profile.
Who should decide: OPEX (business + IT).
Impact if unresolved: Real KB cannot be used; go-live (Test Mode off with real KB) blocked.
```

## O13. Mail providers (wholesale)

```
Decision: Beyond the above, which two/three providers serve the production chain?
Why it matters: Failover acceptance (§11) needs providers 2/3; Sendlib is only confirmed first.
Current status: OPEN (v1.1 §12.3 #14).
Known options: Transactional APIs (several exist) or SMTP; requires domain/DNS decision (§12.2).
Information required: OPEX preferences; domain availability for SPF/DKIM.
Who should decide: Build/maintain team with OPEX visibility.
Impact if unresolved: Cannot build provider 2/3 adapters or validate failover.
```

## O14. Scraping / terms-of-service considerations (per source)

```
Decision: For each source, can it be legally/contractually scraped, or must an API/feed be used?
Why it matters: ToS restrictions exist on some sites; prefer official APIs/feeds (v1.1 §12.3 #11;
v1.0 open question).
Current status: OPEN — developer duty per source before building an adapter; not resolved for
WAHO/UNGM/TenderDetail/ABA in either document.
Known options: Scrape within ToS; use official API/export; skip the source; ask permission.
Information required: Legal review per source; any API/feed availability.
Who should decide: Build/maintain team (+ OPEX where access is paid).
Impact if unresolved: Adapters could violate ToS or fail if blocked.
```

## O15. Knowledge-base document template (content layout)

```
Decision: The exact knowledge-base document template/layout.
Why it matters: §5.5 defines recommended sections but the template is DEFERRED (v1.1 Appendix A);
KB content must be drafted by OPEX.
Current status: DEFERRED — draft before real content is collected; placeholder content used in Test Mode.
Known options: Follow §5.5 suggested sections verbatim; extend as OPEX requires.
Information required: OPEX's real capability/certifications/projects content.
Who should decide: OPEX (content) + build team (template mechanics).
Impact if unresolved: Real KB cannot be loaded; verdict evidence citations lack a stable structure.
```

## O16. "Operations" business alert list

```
Decision: Should business staff receive a subset of system alerts (an operations list)?
Why it matters: v1.1 §5.10.1 marks it optional; alert routing depends on it.
Current status: OPEN (v1.1 §12.3 #2).
Known options: No operations list; or an ops list receiving a chosen subset (e.g. source down 24h).
Information required: OPEX preference.
Who should decide: OPEX business owner.
Impact if unresolved: Alerts go to dev list only; ops visibility depends on devs.
```

## O17. SPF/DKIM / sending-domain DNS setup

```
Decision: Will a custom sending domain be provisioned, and when?
Why it matters: Barrier for any provider that needs a verified domain (positions 2/3); without it
such mail lands in spam (v1.1 §12.2, §5.7).
Current status: DEFERRED (decision needed before adding a domain-requiring provider).
Known options: None for Sendlib; a domain if a conventional provider is chosen.
Information required: OPEX domain availability.
Who should decide: OPEX (IT) + build team.
Impact if unresolved: Provider 2/3 choice constrained to no-domain options; deliverability risk.
```

## O18. Vision-OCR decision

```
Decision: Should scanned WAHO PDFs use a vision-capable model or a dedicated OCR engine by default?
Why it matters: v1.1 §5.4 says test both on real scanned WAHO documents before choosing a default;
the decision gates a v1 role (vision_ocr) and provider capability flags.
Current status: OPEN — to be decided by testing during Phase 1/2.
Known options: Dedicated OCR engine; or LLM page-images with supports_vision.
Information required: Test results on real WAHO scanned PDFs (accuracy, cost).
Who should decide: Build/maintain team, evidenced by tests.
Impact if unresolved: Extraction quality for scanned tenders uncertain.
```

## O19. Test provider for development

```
Decision: DeepSeek (developer's own API access) as the test provider (NVIDIA-hosted DeepSeek is an
alternative) — D2.
Why it matters: All v1 AI development uses it; stays approved_for_company_docs=false.
Current status: CONFIRMED as the test plan (v1.1 §0.2 D2); exact endpoint/credentials are setup details.
Known options: DeepSeek API; NVIDIA-hosted DeepSeek; other OpenAI-compatible endpoint.
Information required: Working credentials + base_url for the chosen endpoint.
Who should decide: Build/maintain team.
Impact if unresolved: AI stages cannot be exercised.
Note: This is confirmed as a plan, listed for completeness — do not treat as user-visible open decision.
```

## O20. Attachments threshold & template additions (URGENT marker, notes footer, link expiry)

```
Decision: Confirm the [PROPOSED] additions to the email template and the link-expiry default (14 days).
Why it matters: §8.1 additions (URGENT subject marker, notes footer) and §5.7 link expiry are labeled
[PROPOSED]; OPEX must accept them as the go-live format.
Current status: [PROPOSED] — awaiting OPEX confirmation (v1.1 D-log & §8).
Known options: Adopt as proposed; or strip to the base §8.1 three-section template only.
Information required: OPEX sign-off on the additions and link-expiry window.
Who should decide: OPEX.
Impact if unresolved: Email format/link security defaults not finalised.
```

## O21. `verdict_failed` tender recovery

```
Decision: Is a Tender with status `verdict_failed` allowed to re-enter the pipeline (e.g. a later
addendum/deadline change)? The spec describes the failure itself (v1.1 §5.6, docs/07 §5: invalid output
after one retry → `verdict_failed`, alert, raw notice still emailed) but defines NO recovery edge.
Why it matters: With no recovery edge, a material change on a `verdict_failed` Tender is rejected by the
Prompt-06 status guard, and every subsequent dedup run for that source errors and rolls back the run
(the affected Tender blocks the whole source until the decision changes or data is amended).
Current status: DECIDED as a documented default — `verdict_failed` is TERMINAL (chosen by the build team,
prompt 06; visible to OPEX). Decision recorded per PROJECT_RULES #5/#19; request a change here to
allow recovery before Prompt 07/10 wiring depends on re-processing.
Known options: (a) terminal as implemented; (b) allow `verdict_failed → updated` when a material change
arrives (docs/04 §4.14 treats updates to previously-seen Tenders as distinct events; no spec text
forbids it); (c) skip re-processing for `verdict_failed` Tenders and count them unchanged.
Who should decide: OPEX (business) with the build/maintain team.
Impact if unresolved: Terminal means a `verdict_failed` Tender with a later addendum blocks its source's
run (loud ERRORED) until an option is chosen. Options (b)/(c) would unblock it.
```

## O22. Unsupported-format attachments: does a skip make the inputs "incomplete"?

```
Decision: When an acquired attachment is in a format the pipeline does not extract (a .xlsx price
schedule, a .png organogram, a legacy .doc), should the tender be reported as having
`incomplete_inputs = true` — or only as having a document that was skipped?
Why it matters: `incomplete_inputs` is not internal bookkeeping. Per docs/04 §4.6 and docs/06 §6.5 it
drives the email footer that tells the reader the verdict was reached on partial inputs. So this
choice decides whether OPEX is told "we could not read everything" when a tender carries a
spreadsheet annex — a business-visible statement about the reliability of the verdict, not an
implementation detail. The spec settles the clear cases (a failed download or a parse failure is a
gap; docs/06 §6.5) but does not say which side a *deliberately unprocessed* format falls on.
Current status: DECIDED as a documented default, flagged for confirmation (prompt 09). Prompt 09 §20
requires distinguishing "declared format is wrong" (a failure: `parse_failed`) from "format is not
supported" (a skip: `unsupported_format`), so unsupported documents are recorded as `skipped`,
retained in `TenderDocumentBundle.skipped_documents`, and do NOT by themselves set
`incomplete_inputs`. Archive containers (`.zip`, whose members are already separate attachment rows
per prompt 08) are treated the same way. Request a change here if OPEX wants unreadable annexes to
mark a tender partial.
Known options: (a) as implemented — skipped, visible in the bundle, `incomplete_inputs` unaffected;
(b) unsupported formats set `incomplete_inputs = true` (strict: anything unread is a gap);
(c) unsupported formats are treated as a per-document failure (`failed` + `unsupported_format`)
rather than a skip; (d) add extraction for the formats that matter (e.g. XLSX) and shrink the set
that reaches this decision at all.
Information required: Which attachment formats actually appear on OPEX's target sources, and how
often a verdict would have changed had one of them been readable.
Who should decide: OPEX (business) with the build/maintain team.
Impact if unresolved: Default (a) is in force. If OPEX would call a spreadsheet annex a material gap,
the email footer understates how partial the inputs were — and the reverse choice would overstate it
for every tender carrying a logo or an organogram.
```

---

## Decision summary tables

| Who | Decisions |
|---|---|
| OPEX (business) | O1, O5, O6, O7, O8, O9, O12, O16, O20, O21, O22 |
| OPEX (business + IT) | O3, O10, O11 |
| Build/maintain team (+ OPEX where relevant) | O2, O4, O13, O14, O18 |
| OPEX (IT) + build team | O17 |
| Build team (evidenced by tests) | O18 |

### Blockers by phase

| Phase | Blocked by |
|---|---|
| Phase 0 | (none — env-seeded dev recipient placeholder allowed; O2 still needs real address) |
| Phase 1 (Test Mode) | O3 (authorised sender mailbox) |
| Phase 2 | O18 (vision/OCR default), O9 (budget value) perhaps O4 (2nd mail provider, Test Mode) |
| Phase 3 | O8 (TenderDetail), O14 (ToS per source), O13 (providers 2/3 if chain required) |
| Phase 4 (go-live) | O1, O3, O4, O9, O12, O11, O16, O20, O22 (partial-input wording), O5/O6/O7 (triage profile) |

## Open-decision discipline

- If a coding prompt hits an OPEN item it cannot avoid, it must implement a clearly-marked configuration default **and** flag the decision rather than silently choosing a business value.
- Never hard-code a resolution for an OPEN decision as if it were a requirement.
- Update this file whenever a decision is resolved — move it to a "Resolved" note or a decision log entry.