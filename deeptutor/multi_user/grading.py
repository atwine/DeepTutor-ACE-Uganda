"""Grading a submitted Assignment answer set.

Reuses the existing AI Judge machinery (``deeptutor/api/routers/quiz_judge.py``)
rather than duplicating its prompts — that module's system prompts and
user-prompt builder are plain functions with no dependency on the live
WebSocket connection they're normally called from, so they're safe to import
and call directly here for a headless, persisted grade instead of a
conversational stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from deeptutor.api.routers.quiz_judge import _JUDGE_SYSTEM_PROMPTS, _build_judge_user_prompt
from deeptutor.services.config.runtime_settings import get_grading_timeout_seconds
from deeptutor.services.llm import complete as llm_complete
from deeptutor.services.llm.exceptions import LLMError

from .assignments import QUESTION_TYPES_AUTO_GRADABLE

logger = logging.getLogger(__name__)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _parse_verdict_fraction(verdict_text: str) -> float:
    """The AI Judge is instructed to open with one of three literal markers
    (see ``_JUDGE_SYSTEM_PROMPTS``). No model response is guaranteed to
    follow instructions perfectly, so an unrecognized opening is treated as
    0 — the full judge text is still stored as feedback, so nothing is
    silently lost, and a false "correct" is a worse failure mode here than a
    false "incorrect" an instructor can review and override by hand."""
    head = verdict_text.strip()[:40]
    if "✅" in head:
        return 1.0
    if "⚠" in head:
        return 0.5
    return 0.0


async def _grade_free_text(
    *,
    question: str,
    question_type: str,
    options: dict[str, str] | None,
    correct_answer: str,
    explanation: str,
    user_answer: str,
    language: str = "en",
) -> tuple[str, float]:
    """Returns (judge_feedback_text, score_fraction 0.0-1.0)."""
    system_prompt = _JUDGE_SYSTEM_PROMPTS.get(language, _JUDGE_SYSTEM_PROMPTS["en"])
    user_prompt = _build_judge_user_prompt(
        language=language,
        question=question,
        question_type=question_type,
        options=options,
        correct_answer=correct_answer,
        explanation=explanation,
        user_answer=user_answer,
        has_image=False,
        image_count=0,
    )
    if not user_answer.strip():
        return "", 0.0
    try:
        verdict_text = await asyncio.wait_for(
            llm_complete(user_prompt, system_prompt=system_prompt),
            timeout=get_grading_timeout_seconds(),
        )
    except TimeoutError:
        # Deliberately shorter and separately messaged than the underlying
        # HTTP client's own 120s timeout (issue #89) — a student waiting on
        # submit shouldn't be stuck behind that, and this is a distinct,
        # more actionable signal for the instructor than a generic provider
        # failure.
        logger.warning("AI Judge grading call timed out")
        return "AI grading timed out — an instructor will review this.", 0.0
    except LLMError as exc:
        logger.warning("AI Judge grading call failed: %s", exc)
        return "AI grading is temporarily unavailable — an instructor will need to grade this by hand.", 0.0
    return verdict_text, _parse_verdict_fraction(verdict_text)


async def grade_submission(
    assignment: dict[str, Any],
    answers: list[dict[str, Any]],
    *,
    language: str = "en",
) -> tuple[list[dict[str, Any]], float, float]:
    """Grade every question in ``assignment`` against the submitted
    ``answers`` (each ``{"question_id": ..., "answer": ...}``).

    Returns ``(question_results, score, max_score)``. ``question_results`` is
    one entry per question: ``{question_id, question, user_answer,
    is_correct, score, max_score, feedback}`` — enough for both the
    student's own result view and an instructor's gradebook row.
    """
    answers_by_question = {
        str(a.get("question_id")): str(a.get("answer") or "") for a in answers
    }
    results: list[dict[str, Any]] = []
    total_score = 0.0
    total_max = 0.0

    for question in assignment.get("questions", []):
        qid = question.get("question_id", "")
        # Issue #65: same `0 or 1.0` fix as assignments.py's _normalize_question
        # -- an explicit `points: 0` question was silently regraded worth 1.
        points = float(question["points"]) if question.get("points") is not None else 1.0
        user_answer = answers_by_question.get(qid, "")
        question_type = question.get("question_type", "")
        total_max += points

        if question_type in QUESTION_TYPES_AUTO_GRADABLE:
            is_correct = _normalize(user_answer) == _normalize(question.get("correct_answer", ""))
            score = points if is_correct else 0.0
            feedback = question.get("explanation", "") if question.get("explanation") else ""
            results.append(
                {
                    "question_id": qid,
                    "question": question.get("question", ""),
                    "user_answer": user_answer,
                    "is_correct": is_correct,
                    "score": score,
                    "max_score": points,
                    "feedback": feedback,
                }
            )
        else:
            feedback, fraction = await _grade_free_text(
                question=question.get("question", ""),
                question_type=question_type,
                options=question.get("options"),
                correct_answer=question.get("correct_answer", ""),
                explanation=question.get("explanation", ""),
                user_answer=user_answer,
                language=language,
            )
            score = points * fraction
            results.append(
                {
                    "question_id": qid,
                    "question": question.get("question", ""),
                    "user_answer": user_answer,
                    "is_correct": fraction >= 1.0,
                    "score": score,
                    "max_score": points,
                    "feedback": feedback,
                }
            )
        total_score += results[-1]["score"]

    return results, total_score, total_max
