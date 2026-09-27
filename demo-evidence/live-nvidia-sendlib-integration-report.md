# Live NVIDIA + Sendlib integration report

**Final gate: LIVE NVIDIA + SENDLIB E2E: NEEDS FIXES**

## Environment and runtime

- Date: 2026-09-26 (Africa/Lagos)
- Repository commit observed: `16b6543` (working tree contains changes)
- Runtime: Windows workspace; Python virtual environment; Admin API configured on `127.0.0.1:8000` in the supplied operator log.
- Context7 MCP was requested but is not available among the tools connected to this session. NVIDIA's official model documentation was consulted instead.

## Provider configuration (secret values omitted)

The implementation uses the existing `LLMClient` abstraction and an OpenAI-compatible HTTP client. The configured profile observed through the local Admin API was named `NVIDIA`, used base URL `https://integrate.api.nvidia.com/v1`, model `nvidia/nemotron-3.5-lightning-30b-a3b`, had an encrypted key configured, and was marked approved for company documents. No key, credential, recipient, or sender value is included here.

The client now supports configured `top_p`, `enable_thinking`, and `reasoning_budget`; caps reasoning budget at the effective output-token limit; avoids requesting `response_format` for NVIDIA; validates output through existing stage schemas; and records safe request configuration with LLM-call provenance. The existing generic `supports_json` behavior remains for non-NVIDIA endpoints.

## Acceptance matrix

| Test | Type | Actual result | Status |
|---|---|---|---|
| NVIDIA client construction | Offline integration/unit | Client construction and request shaping covered by tests | PASS |
| NVIDIA live smoke call | Live | Not completed; the prior Admin test timed out and the local Admin process then stopped responding | NOT VERIFIED |
| Admin NVIDIA connection test | Live | Operator log shows HTTP 502 `provider_test_failed`; current live retry could not be performed because the local service was unavailable | FAIL / UNVERIFIED |
| Stage A | Live | Not run against NVIDIA | NOT RUN |
| Stage B | Live | Not run against NVIDIA | NOT RUN |
| Verdict persistence | Live | Not run against NVIDIA | NOT RUN |
| Sendlib connection | Live | No live provider call made | NOT RUN |
| Email notification | Live | No real email sent | NOT RUN |
| End-to-end tender | Live | Not run | NOT RUN |
| NVIDIA auth failure | Mocked | No new isolated live check; existing client error mapping preserved | NOT VERIFIED HERE |
| NVIDIA timeout / 429 | Mocked | No new isolated live check; existing client error mapping preserved | NOT VERIFIED HERE |
| Malformed output | Mocked | Existing validation/retry paths unchanged | NOT VERIFIED HERE |
| Sendlib failure | Mocked | Not run in this focused verification | NOT VERIFIED HERE |
| Test Mode enforcement | Automated | Not exercised in this focused verification | NOT VERIFIED HERE |
| Provenance | Offline tests | Safe request configuration is recorded; integration tests passed | PASS (offline only) |

## Trace and diagnosis

The supplied log shows `POST /api/v1/llm/profiles/1/test` returning 502. Repeated `GET /v1/models` 404s are separate: the local OPEX Admin app does not expose that provider route. The OPEX connection test calls chat completions through the configured client; the `/v1/models` 404 alone does not explain the connection-test failure.

The connection test previously reported only a generic provider failure, which hid the useful safe category. The Admin/UI path now surfaces a sanitized provider category without returning raw provider response bodies or secrets. The NVIDIA client was adjusted to match the official NVIDIA chat-completions request contract more closely, especially by omitting `response_format` and mapping NVIDIA reasoning controls. The suspected request incompatibility is an inference; no successful real NVIDIA response was obtained to confirm it.

An attempted local live retry exceeded the time limit and the Admin service became unreachable. This environment could not reach the operator's local service/database afterwards, so no root cause beyond the observed 502 and timeout can be claimed. No live tender, Stage A/B, verdict, Sendlib request, or email was executed.

## Provenance, usage, safety, and limitations

- Safe request parameters (temperature, top_p, effective max tokens, thinking flag, and effective reasoning budget) are now carried in LLM-call provenance.
- Provider response bodies and reasoning traces are not retained by the client.
- API keys remain write-only/encrypted through the existing secret path and are not included in artifacts.
- Live token usage/cost is unavailable because no live response was received.
- No Test Mode transition occurred; no production email was sent.
- Migration `0014_llm_sampling_provenance` adds the profile controls and request-provenance field. Apply repository migrations and restart the Admin process before trying the updated UI/profile controls.
- Configure NVIDIA `top_p=0.95`, enable thinking only as intended, and set a reasoning budget no greater than the operation's configured output limit. Do not expose credentials in chat or logs.
- A complete acceptance run still requires the operator's configured live provider connectivity and Sendlib credentials/recipient, then execution of the controlled test workflow.

## Verification performed

- `pytest tests/unit/test_openai_compatible_llm.py tests/integration/test_admin_api_prompt15.py tests/integration/test_verdict_engine.py`: **32 passed**.
- `node --test tests/ui/live-api.test.mjs`: **11 passed**.
- Ruff on the changed Python and migration files: **passed**.
- These are offline checks and do not establish live provider or email delivery.

## Final gate

LIVE NVIDIA + SENDLIB E2E: NEEDS FIXES
