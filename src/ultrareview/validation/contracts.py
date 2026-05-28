from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_SEVERITIES = {"critical", "must_change", "better_to_change", "acceptable"}
VALID_VERDICTS = {"verified", "rejected", "uncertain"}


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]


def _required(data: dict[str, Any], fields: list[str]) -> list[str]:
    return [f"missing required field: {field}" for field in fields if field not in data]


def _validate_evidence(data: dict[str, Any]) -> list[str]:
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return ["evidence must be a non-empty list"]

    errors: list[str] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"evidence[{index}] must be an object")
            continue
        for field in ("path", "line", "quote"):
            if field not in item:
                errors.append(f"evidence[{index}] missing field: {field}")
        if "line" in item and (not isinstance(item["line"], int) or item["line"] <= 0):
            errors.append(f"evidence[{index}].line must be a positive integer")
    return errors


def validate_candidate(candidate: dict[str, Any]) -> ValidationResult:
    required = [
        "title",
        "category",
        "severity",
        "confidence",
        "file",
        "line",
        "introduced_by_diff",
        "claim",
        "failure_mode",
        "evidence",
        "suggested_fix",
    ]
    errors = _required(candidate, required)

    severity = candidate.get("severity")
    if severity is not None and severity not in VALID_SEVERITIES:
        errors.append(f"invalid severity: {severity}")

    confidence = candidate.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, int) or confidence < 0 or confidence > 100
    ):
        errors.append("confidence must be an integer from 0 to 100")

    line = candidate.get("line")
    if line is not None and (not isinstance(line, int) or line <= 0):
        errors.append("line must be a positive integer")

    failure_mode = candidate.get("failure_mode")
    if candidate.get("severity") in {"critical", "must_change"} and not failure_mode:
        errors.append("failure_mode is required for critical and must_change findings")

    errors.extend(_validate_evidence(candidate))
    return ValidationResult(valid=not errors, errors=errors)


def validate_verification(verification: dict[str, Any]) -> ValidationResult:
    required = ["candidate_id", "verdict", "reason", "evidence"]
    errors = _required(verification, required)

    verdict = verification.get("verdict")
    if verdict is not None and verdict not in VALID_VERDICTS:
        errors.append(f"invalid verdict: {verdict}")

    if not verification.get("reason"):
        errors.append("reason must be non-empty")

    errors.extend(_validate_evidence(verification))
    return ValidationResult(valid=not errors, errors=errors)

