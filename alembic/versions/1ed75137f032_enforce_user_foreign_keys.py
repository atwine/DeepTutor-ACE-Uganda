"""enforce user foreign keys

Revision ID: 1ed75137f032
Revises: 5726a98947e7
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ed75137f032'
down_revision: Union[str, None] = '5726a98947e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every user_id-shaped column below used to be a plain string with no
    # real link back to `users` — a decision made when accounts still lived
    # in a JSON file (a database FK can't point at a file). Accounts moved
    # into this same database in the #53 migration, so that reason no
    # longer applies. This closes the gap: a bug, a bad script, or a raw
    # DELETE can no longer leave a course, a grade, or an access grant
    # silently pointing at an account that doesn't exist.
    #
    # Defensively clean up any rows that already reference a nonexistent
    # user before adding the constraint — this environment has none (SELECT
    # already confirmed it), but a migration that ADD CONSTRAINTs without
    # this would simply fail outright on any environment where issue #66
    # (deleting a user left AssignmentAccessGrant/NotificationRead rows
    # behind) already happened.
    op.execute(
        "DELETE FROM course_unit_instructors cui "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = cui.instructor_id)"
    )
    op.execute(
        "DELETE FROM enrollments e "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = e.user_id)"
    )
    op.execute(
        "DELETE FROM submissions s "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = s.user_id)"
    )
    op.execute(
        "DELETE FROM assignment_access_grants g "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = g.user_id)"
    )
    op.execute(
        "UPDATE assignment_access_grants g SET granted_by = NULL "
        "WHERE granted_by IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = g.granted_by)"
    )
    op.execute(
        "DELETE FROM notification_reads n "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = n.user_id)"
    )

    op.create_foreign_key(
        "fk_course_unit_instructors_instructor_id_users",
        "course_unit_instructors", "users",
        ["instructor_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_enrollments_user_id_users",
        "enrollments", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_submissions_user_id_users",
        "submissions", "users",
        ["user_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_assignment_access_grants_user_id_users",
        "assignment_access_grants", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column(
        "assignment_access_grants", "granted_by",
        existing_type=sa.String(), nullable=True,
    )
    op.create_foreign_key(
        "fk_assignment_access_grants_granted_by_users",
        "assignment_access_grants", "users",
        ["granted_by"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_notification_reads_user_id_users",
        "notification_reads", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_notification_reads_user_id_users", "notification_reads", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_assignment_access_grants_granted_by_users",
        "assignment_access_grants", type_="foreignkey",
    )
    op.alter_column(
        "assignment_access_grants", "granted_by",
        existing_type=sa.String(), nullable=False,
    )
    op.drop_constraint(
        "fk_assignment_access_grants_user_id_users",
        "assignment_access_grants", type_="foreignkey",
    )
    op.drop_constraint(
        "fk_submissions_user_id_users", "submissions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_enrollments_user_id_users", "enrollments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_course_unit_instructors_instructor_id_users",
        "course_unit_instructors", type_="foreignkey",
    )
