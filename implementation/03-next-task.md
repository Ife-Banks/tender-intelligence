# Current Next Task

## Prompt 16C — Authentication Scope Removal & Admin Access Simplification

- **Implementation:** complete in the working tree; independent verification is the next step.
- **Current access scope:** Admin UI/API do not require application login. O11 is deferred and non-blocking for the current internal-tool version. No production identity provider is configured.
- **Preserved controls:** Test Mode and development-recipient routing, server-side validation, write-only encrypted secrets, configuration audit trail, dry-run behavior, and worker independence.
- **Prompt 17:** not started. Do not implement security-hardening scope as part of 16C.
- **Prompt 18/19:** not started.
- **Known suite issue:** `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` remains a known unrelated failure; do not fold it into 16C.
- **Verification debt:** previous independent verification gaps for Prompts 14, 15, 16A, and 16B remain as documented; this implementation does not close them.

See `demo-evidence/prompt-16c-implementation-report.md` for file, test, and scope details.
