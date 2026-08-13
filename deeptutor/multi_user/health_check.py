"""Data-integrity health check (issue #79).

Foreign-key constraints (issue #66's fix) catch dangling *references* --
a row pointing at another row that no longer exists. They can't catch a
row that's internally *inconsistent* with what it claims: an enrollment
marked complete whose submissions don't actually support that, a course
with nobody teaching it, an assignment that can never be passed. Nothing
stops these from happening, and nothing was watching for them.

This is a second line of defense, not a replacement for the FK hardening
or the #63/#64/#65 correctness fixes -- it exists to catch anything that
slipped through before those fixes landed, or any future path that
doesn't go through the code paths those fixes cover.

Usage:
    from deeptutor.multi_user.health_check import run_all_checks
    findings = await run_all_checks()

Each finding is a plain dict: ``{"check": ..., "detail": ..., **ids}``.
An empty list means nothing wrong was found.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from deeptutor.services.db import session_scope
from deeptutor.services.db.models import (
    Assignment,
    CourseUnit,
    CourseUnitInstructor,
    Enrollment,
    Submission,
)


async def check_stale_completions() -> list[dict[str, Any]]:
    """Enrollments marked ``completed_at`` where the underlying submissions
    no longer actually support it.

    Issue #63's fix (clearing ``completed_at`` when a required assignment's
    submission is reset) should make new cases of this structurally
    impossible going forward -- this catches anything left over from
    before that fix, or any other write path that sets ``completed_at``
    without going through ``check_and_mark_completion``.
    """
    findings: list[dict[str, Any]] = []
    async with session_scope() as session:
        completed = (
            await session.execute(
                select(Enrollment).where(Enrollment.completed_at.isnot(None))
            )
        ).scalars().all()
        if not completed:
            return findings

        course_unit_ids = {e.course_unit_id for e in completed}
        required_assignments = (
            await session.execute(
                select(Assignment.id, Assignment.course_unit_id).where(
                    Assignment.course_unit_id.in_(course_unit_ids),
                    Assignment.status == "published",
                    Assignment.is_optional.is_(False),
                )
            )
        ).all()
        required_by_course: dict[str, list[str]] = {}
        for assignment_id, course_unit_id in required_assignments:
            required_by_course.setdefault(course_unit_id, []).append(assignment_id)

        all_required_ids = [aid for ids in required_by_course.values() for aid in ids]
        submitted_pairs: set[tuple[str, str]] = set()
        if all_required_ids:
            user_ids = {e.user_id for e in completed}
            rows = (
                await session.execute(
                    select(Submission.assignment_id, Submission.user_id).where(
                        Submission.assignment_id.in_(all_required_ids),
                        Submission.user_id.in_(user_ids),
                    )
                )
            ).all()
            submitted_pairs = {(row.assignment_id, row.user_id) for row in rows}

        for enrollment in completed:
            required_ids = required_by_course.get(enrollment.course_unit_id, [])
            missing = [
                aid for aid in required_ids if (aid, enrollment.user_id) not in submitted_pairs
            ]
            if missing:
                findings.append(
                    {
                        "check": "stale_completion",
                        "course_unit_id": enrollment.course_unit_id,
                        "user_id": enrollment.user_id,
                        "detail": (
                            f"marked complete but missing submissions for "
                            f"{len(missing)} required assignment(s)"
                        ),
                    }
                )
    return findings


async def check_courses_without_instructors() -> list[dict[str, Any]]:
    """Active (non-archived) course units with zero instructors assigned --
    nobody can approve enrollments, grade, or manage materials for it."""
    findings: list[dict[str, Any]] = []
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(CourseUnit.id, CourseUnit.name)
                .outerjoin(
                    CourseUnitInstructor,
                    CourseUnitInstructor.course_unit_id == CourseUnit.id,
                )
                .where(
                    CourseUnitInstructor.instructor_id.is_(None),
                    CourseUnit.is_archived.is_(False),
                )
            )
        ).all()
        for course_unit_id, name in rows:
            findings.append(
                {
                    "check": "course_without_instructor",
                    "course_unit_id": course_unit_id,
                    "detail": f"'{name}' has zero instructors assigned",
                }
            )
    return findings


async def check_broken_assignments() -> list[dict[str, Any]]:
    """Published assignments that can never actually be graded/passed:
    a ``passing_score`` set with no questions to grade, or every question
    worth 0 points (max_score of 0 breaks percentage math downstream)."""
    findings: list[dict[str, Any]] = []
    async with session_scope() as session:
        assignments = (
            await session.execute(select(Assignment).where(Assignment.status == "published"))
        ).scalars().all()
        for assignment in assignments:
            questions = assignment.questions or []
            if assignment.passing_score is not None and not questions:
                findings.append(
                    {
                        "check": "passing_score_no_questions",
                        "assignment_id": assignment.id,
                        "course_unit_id": assignment.course_unit_id,
                        "detail": (
                            f"'{assignment.title}' has passing_score="
                            f"{assignment.passing_score} but zero questions"
                        ),
                    }
                )
                continue
            if not questions:
                continue
            # Same "not provided defaults to 1.0, explicit 0 stays 0" rule
            # as issue #65's fix -- an assignment can legitimately mix in a
            # 0-point practice question, but *every* question being 0
            # leaves max_score at 0, which breaks any percentage/passing
            # calculation over it.
            total_points = sum(
                float(q["points"]) if q.get("points") is not None else 1.0
                for q in questions
            )
            if total_points <= 0:
                findings.append(
                    {
                        "check": "all_zero_point_questions",
                        "assignment_id": assignment.id,
                        "course_unit_id": assignment.course_unit_id,
                        "detail": (
                            f"'{assignment.title}' has {len(questions)} question(s) "
                            f"all worth 0 points -- max_score is 0"
                        ),
                    }
                )
    return findings


async def run_all_checks() -> list[dict[str, Any]]:
    """Run every check and return the combined findings list (empty if
    everything is consistent)."""
    findings: list[dict[str, Any]] = []
    findings.extend(await check_stale_completions())
    findings.extend(await check_courses_without_instructors())
    findings.extend(await check_broken_assignments())
    return findings


__all__ = [
    "check_stale_completions",
    "check_courses_without_instructors",
    "check_broken_assignments",
    "run_all_checks",
]
