from __future__ import annotations

from ultrareview.validation.contracts import (
    validate_candidate,
    validate_verification,
)


VALID_CANDIDATE = {
    "title": "Tenant check compares user to itself",
    "category": "security",
    "severity": "must_change",
    "confidence": 90,
    "file": "app.py",
    "line": 12,
    "introduced_by_diff": "The changed comparison now checks the user tenant against itself.",
    "claim": "The invoice tenant is not checked.",
    "failure_mode": "A user can view another tenant's invoice.",
    "evidence": [{"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}],
    "suggested_fix": "Compare user.company_id to invoice.company_id.",
}


def test_valid_candidate_passes():
    result = validate_candidate(VALID_CANDIDATE)

    assert result.valid is True
    assert result.errors == []


def test_candidate_rejects_boolean_introduced_by_diff():
    candidate = {**VALID_CANDIDATE, "introduced_by_diff": True}

    result = validate_candidate(candidate)

    assert result.valid is False
    assert "introduced_by_diff must be a non-empty string" in result.errors


def test_candidate_rejects_empty_introduced_by_diff():
    candidate = {**VALID_CANDIDATE, "introduced_by_diff": "   "}

    result = validate_candidate(candidate)

    assert result.valid is False
    assert "introduced_by_diff must be a non-empty string" in result.errors


def test_candidate_without_file_fails():
    candidate = {**VALID_CANDIDATE}
    candidate.pop("file")

    result = validate_candidate(candidate)

    assert result.valid is False
    assert "missing required field: file" in result.errors


def test_candidate_without_line_fails():
    candidate = {**VALID_CANDIDATE}
    candidate.pop("line")

    result = validate_candidate(candidate)

    assert result.valid is False
    assert "missing required field: line" in result.errors


def test_candidate_without_evidence_fails():
    candidate = {**VALID_CANDIDATE, "evidence": []}

    result = validate_candidate(candidate)

    assert result.valid is False
    assert "evidence must be a non-empty list" in result.errors


def test_candidate_with_invalid_severity_fails():
    candidate = {**VALID_CANDIDATE, "severity": "catastrophic"}

    result = validate_candidate(candidate)

    assert result.valid is False
    assert "invalid severity: catastrophic" in result.errors


def test_verification_with_invalid_verdict_fails():
    verification = {
        "candidate_id": "cand_123",
        "verdict": "maybe",
        "reason": "unclear",
        "evidence": [{"path": "app.py", "line": 12, "quote": "x"}],
    }

    result = validate_verification(verification)

    assert result.valid is False
    assert "invalid verdict: maybe" in result.errors
