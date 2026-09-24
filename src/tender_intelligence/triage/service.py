"""Stage A triage service (docs/07 §7.1; Prompt 13)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.core.errors import AI_CALL_TIMEOUT
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.interfaces.llm import LLMClient, LLMError, LLMMessage

TRIAGE_ROLE = "triage"
PASS = "passed"
DISCARDED = "triage_discarded"
FAILED = "triage_failed"
DEFAULT_MODEL_RETRIES = 2


class TriageError(Exception):
    """A triage evaluation could not produce a valid decision."""

    def __init__(self, message: str, error_code: str = AI_CALL_TIMEOUT) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


@dataclass(frozen=True)
class TriageDecision:
    tender_id: int
    correlation_id: str
    run_id: int | None
    status: str
    score: float | None
    mode: str
    reasons: tuple[str, ...]
    result_id: int
    llm_profile_id: int | None = None
    model: str | None = None
    error_code: str | None = None

    @property
    def proceeds_to_verdict(self) -> bool:
        return self.status == PASS


LLMClientFactory = Callable[[LLMProfile], LLMClient]


class TriageService:
    """Evaluate a tender with configured rules, optionally followed by a cheap model.

    A configured triage assignment selects model mode. No assignment means pure rule mode.
    The compact tender title/metadata prompt never contains KB or document-bundle content.
    """

    def __init__(
        self,
        session: Session,
        *,
        client_factory: LLMClientFactory | None = None,
        run_id: int | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self._client_factory = client_factory
        self._run_id = run_id
        self._clock = clock
        self._sleeper = sleeper

    def evaluate(self, tender_id: int) -> TriageDecision:
        # Callers commonly save a Settings or role-assignment change in this same unit of
        # work immediately before evaluating. Make that pending configuration visible before
        # resolving the role, without committing the caller's transaction.
        self.session.flush()
        tender = self.session.get(Tender, tender_id)
        if tender is None:
            raise TriageError(f"unknown tender {tender_id}", "triage_tender_not_found")
        try:
            rules, threshold = self._rules()
        except TriageError as exc:
            return self._persist_failure(tender, mode="rules", error_code=exc.error_code)
        rule_decision = _evaluate_rules(tender, rules, threshold)
        assignment = self.session.scalars(
            select(LLMRoleAssignment).where(LLMRoleAssignment.role == TRIAGE_ROLE)
        ).one_or_none()
        if assignment is None:
            return self._persist(tender, rule_decision, mode="rules")
        if self._client_factory is None:
            return self._persist_failure(
                tender, mode="model", error_code="triage_client_unavailable"
            )
        profile = self.session.get(LLMProfile, assignment.profile_id)
        if profile is None or not profile.active:
            return self._persist_failure(
                tender, mode="model", error_code="triage_profile_unavailable"
            )
        fallback = (
            self.session.get(LLMProfile, assignment.fallback_profile_id)
            if assignment.fallback_profile_id is not None
            else None
        )
        if fallback is not None and (not fallback.active or fallback.id == profile.id):
            fallback = None
        return self._evaluate_model(tender, profile, rule_decision, fallback)

    def _rules(self) -> tuple[dict[str, Any], float | None]:
        settings = self.session.get(Setting, 1)
        if settings is None:
            return {}, None
        raw_rules = settings.triage_rules
        if raw_rules is not None and not isinstance(raw_rules, dict):
            raise TriageError("invalid triage rules configuration", "triage_invalid_config")
        rules = dict(raw_rules or {})
        for key in ("include_keywords", "exclude_keywords", "target_sectors", "target_regions"):
            value = rules.get(key)
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                raise TriageError("invalid triage rules configuration", "triage_invalid_config")
        minimum = rules.get("minimum_contract_value")
        if minimum is not None and (
            not isinstance(minimum, (int, float))
            or isinstance(minimum, bool)
            or not math.isfinite(minimum)
        ):
            raise TriageError("invalid triage rules configuration", "triage_invalid_config")
        threshold = settings.triage_threshold
        if threshold is not None and (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not math.isfinite(threshold)
        ):
            raise TriageError("invalid triage threshold configuration", "triage_invalid_config")
        return rules, float(threshold) if threshold is not None else None

    def _evaluate_model(
        self,
        tender: Tender,
        profile: LLMProfile,
        rule_decision: _RuleDecision,
        fallback: LLMProfile | None,
    ) -> TriageDecision:
        candidates = (profile, fallback) if fallback is not None else (profile,)
        last_error = AI_CALL_TIMEOUT
        active_profile = profile
        response = None
        decision = "discard"
        score = None
        for candidate in candidates:
            active_profile = candidate
            for retry in range(DEFAULT_MODEL_RETRIES + 1):
                started = self._clock()
                try:
                    client = self._client_factory(candidate)  # type: ignore[misc]
                    response = client.chat(_model_messages(tender, rule_decision))
                    decision, score = _parse_model_response(response.content)
                    _, threshold = self._rules()
                    if threshold is not None and score is None:
                        raise TriageError(
                            "triage score required by configured threshold", "triage_missing_score"
                        )
                except LLMError as exc:
                    last_error = exc.error_code
                    self._record_call(
                        tender,
                        candidate,
                        status="failed",
                        error_code=last_error,
                        duration_ms=_elapsed_ms(started, self._clock()),
                    )
                    if last_error == AI_CALL_TIMEOUT and retry < DEFAULT_MODEL_RETRIES:
                        self._sleeper(0.25 * (2**retry))
                        continue
                    if last_error == AI_CALL_TIMEOUT:
                        break
                    return self._persist_failure(
                        tender, mode="model", error_code=last_error, profile=candidate
                    )
                except TriageError as exc:
                    self._record_call(
                        tender,
                        candidate,
                        status="failed",
                        error_code=exc.error_code,
                        duration_ms=_elapsed_ms(started, self._clock()),
                    )
                    return self._persist_failure(
                        tender, mode="model", error_code=exc.error_code, profile=candidate
                    )
                except Exception:  # noqa: BLE001 - provider seam must fail closed
                    self._record_call(
                        tender,
                        candidate,
                        status="failed",
                        error_code=AI_CALL_TIMEOUT,
                        duration_ms=_elapsed_ms(started, self._clock()),
                    )
                    return self._persist_failure(
                        tender, mode="model", error_code=AI_CALL_TIMEOUT, profile=candidate
                    )
                else:
                    self._record_call(
                        tender,
                        candidate,
                        status="success",
                        error_code=None,
                        duration_ms=_elapsed_ms(started, self._clock()),
                        tokens_in=response.usage.prompt_tokens if response.usage else None,
                        tokens_out=response.usage.completion_tokens if response.usage else None,
                        est_cost=response.usage.estimated_cost_usd if response.usage else None,
                    )
                    break
            if response is not None:
                break
        if response is None:
            return self._persist_failure(
                tender, mode="model", error_code=last_error, profile=active_profile
            )
        model_result = _RuleDecision(
            status=(
                DISCARDED
                if decision == "discard"
                or (threshold is not None and score is not None and score < threshold)
                else PASS
            ),
            score=score,
            reasons=("model_pass" if decision == "pass" else "model_discard",),
        )
        return self._persist(tender, model_result, mode="model", profile=active_profile)

    def _record_call(
        self,
        tender: Tender,
        profile: LLMProfile,
        *,
        status: str,
        error_code: str | None,
        duration_ms: int,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        est_cost: float | None = None,
    ) -> None:
        self.session.add(
            LLMCall(
                correlation_id=tender.correlation_id,
                role=TRIAGE_ROLE,
                profile_id=profile.id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=duration_ms,
                est_cost=est_cost,
                status=status,
                error_code=error_code,
            )
        )

    def _persist(
        self,
        tender: Tender,
        decision: _RuleDecision,
        *,
        mode: str,
        profile: LLMProfile | None = None,
    ) -> TriageDecision:
        result = TriageResult(
            tender_id=tender.id,
            run_id=self._run_id,
            correlation_id=tender.correlation_id,
            status=decision.status,
            score=decision.score,
            mode=mode,
            reasons=list(decision.reasons),
            llm_profile_id=profile.id if profile else None,
            model=profile.model if profile else None,
        )
        self.session.add(result)
        self.session.flush()
        return TriageDecision(
            tender_id=tender.id,
            run_id=self._run_id,
            correlation_id=tender.correlation_id,
            status=decision.status,
            score=decision.score,
            mode=mode,
            reasons=decision.reasons,
            result_id=result.id,
            llm_profile_id=profile.id if profile else None,
            model=profile.model if profile else None,
        )

    def _persist_failure(
        self,
        tender: Tender,
        *,
        mode: str,
        error_code: str,
        profile: LLMProfile | None = None,
    ) -> TriageDecision:
        result = TriageResult(
            tender_id=tender.id,
            run_id=self._run_id,
            correlation_id=tender.correlation_id,
            status=FAILED,
            score=None,
            mode=mode,
            reasons=["evaluation_failed"],
            llm_profile_id=profile.id if profile else None,
            model=profile.model if profile else None,
            error_code=error_code,
        )
        self.session.add(result)
        self.session.flush()
        return TriageDecision(
            tender_id=tender.id,
            run_id=self._run_id,
            correlation_id=tender.correlation_id,
            status=FAILED,
            score=None,
            mode=mode,
            reasons=("evaluation_failed",),
            result_id=result.id,
            llm_profile_id=profile.id if profile else None,
            model=profile.model if profile else None,
            error_code=error_code,
        )


@dataclass(frozen=True)
class _RuleDecision:
    status: str
    score: float | None
    reasons: tuple[str, ...]


def _evaluate_rules(
    tender: Tender, rules: dict[str, Any], threshold: float | None
) -> _RuleDecision:
    metadata = _public_metadata(tender.raw_metadata or {})
    text = " ".join([tender.title, *(_text_values(metadata))]).casefold()
    reasons: list[str] = []

    excludes = _strings(rules.get("exclude_keywords"))
    if any(value in text for value in excludes):
        return _RuleDecision(DISCARDED, None, ("exclude_keyword",))

    includes = _strings(rules.get("include_keywords"))
    if any(value in text for value in includes):
        reasons.append("include_keyword")

    for key, reason in (("target_sectors", "sector"), ("target_regions", "region")):
        targets = _strings(rules.get(key))
        if not targets:
            continue
        candidate = str(metadata.get(reason, "")).casefold()
        if not candidate or not any(value in candidate or value in text for value in targets):
            return _RuleDecision(DISCARDED, None, (f"{reason}_outside_target",))
        reasons.append(f"{reason}_target_match")

    minimum = rules.get("minimum_contract_value")
    if minimum is not None:
        try:
            raw_contract_value = metadata.get("contract_value")
            if raw_contract_value is None:
                raise ValueError("contract value is absent")
            contract_value = float(raw_contract_value)
            if contract_value < float(minimum):
                return _RuleDecision(DISCARDED, None, ("contract_value_below_minimum",))
            reasons.append("contract_value_meets_minimum")
        except (TypeError, ValueError):
            reasons.append("contract_value_unavailable")

    return _RuleDecision(
        PASS,
        None,
        tuple(reasons) or ("no_rule_disqualifier",),
    )


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip().lower() for item in value if str(item).strip())


def _text_values(metadata: dict[str, Any]) -> list[str]:
    return [str(value) for value in metadata.values() if isinstance(value, (str, int, float))]


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Use an explicit allowlist; arbitrary source metadata can contain internal fields."""
    allowed = {"source_name", "sector", "region", "contract_value", "notice_text"}
    return {key: value for key, value in metadata.items() if key in allowed}


def _model_messages(tender: Tender, rule_decision: _RuleDecision) -> list[LLMMessage]:
    metadata = _public_metadata(tender.raw_metadata or {})
    payload = {
        "title": tender.title,
        "source_metadata": {
            key: value for key, value in metadata.items() if isinstance(value, (str, int, float))
        },
        "rule_score": rule_decision.score,
    }
    return [
        LLMMessage(
            role="system",
            content=(
                "Classify this public tender notice for relevance. Return only JSON with "
                '"decision" set to "pass" or "discard", and optional numeric "score".'
            ),
        ),
        LLMMessage(role="user", content=json.dumps(payload, sort_keys=True)),
    ]


def _parse_model_response(content: str) -> tuple[str, float | None]:
    try:
        data = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise TriageError("triage model returned invalid JSON", "triage_invalid_output") from exc
    if not isinstance(data, dict) or data.get("decision") not in {"pass", "discard"}:
        raise TriageError("triage model returned invalid decision", "triage_invalid_output")
    score = data.get("score")
    if score is not None and (
        not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score)
    ):
        raise TriageError("triage model returned invalid score", "triage_invalid_output")
    return str(data["decision"]), float(score) if score is not None else None


def _elapsed_ms(started: float, ended: float) -> int:
    return max(0, round((ended - started) * 1000))
