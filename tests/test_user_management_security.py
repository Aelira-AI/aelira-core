"""Security regressions for administrative user deactivation."""

import asyncio
from unittest.mock import MagicMock

from src.api.user_management import remove_user
from src.db.models import User, UserRole


def test_admin_removal_clears_lti_reauthorization_marker():
    user = MagicMock(spec=User)
    user.id = "target-user"
    user.email = "faculty@example.edu"
    user.role = UserRole.FACULTY
    user.is_active = True
    user.lti_reauthorization_required = True
    user.deactivated_at = None

    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = user
    db = MagicMock()
    db.query.return_value = query

    result = asyncio.run(
        remove_user(
            "target-user",
            db=db,
            admin_info=(None, "admin-user", "dept-1", UserRole.ADMIN),
        )
    )

    assert result["success"] is True
    assert user.is_active is False
    assert user.lti_reauthorization_required is False
    assert user.deactivated_at is not None
    db.commit.assert_called_once()
