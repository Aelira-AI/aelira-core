"""
Tests for CanvasLTIService.extract_launch_data() against realistic Canvas
LTI 1.3 id_token payloads (decoded JWT shape, not the raw form-encoded
LTI 1.1 style).

Written 2026-08-18 while chasing a report that launches still landed
course-less after the developer key's custom_fields (canvas_course_id =
$Canvas.course.id) were confirmed configured in Canvas. Two things were
verified/fixed here:

1. The custom claim extraction itself has no "prefixing games" bug —
   Canvas's LTI 1.3 custom claim
   (https://purl.imsglobal.org/spec/lti/claim/custom) carries members
   unprefixed (e.g. "canvas_course_id", not "custom_canvas_course_id" —
   that "custom_" prefix convention only applies to LTI 1.1's flat
   form-encoded launch parameters). extract_launch_data() reads the claim
   correctly and surfaces it unprefixed via custom_params.

2. A real bug WAS found and fixed: when the canvas_course_id custom
   variable wasn't substituted (or the custom claim was missing
   entirely), the function fell back to the LTI `context` claim's `id`.
   Canvas documents that field as an OPAQUE per-deployment identifier for
   the launch context — NOT the numeric Canvas course id used by the
   Canvas REST API and by this app's own CloudFile.provider_parent_id
   matching. Using it as course_id silently produced a plausible-looking
   but functionally wrong id, indistinguishable from "no course" once
   every downstream lookup came back empty. The fallback is removed —
   course_id is now empty (not the opaque context id) whenever the
   custom variable isn't resolvable, which correctly triggers the
   /lti/overview safety net in lti_launch_handler.py.
"""

from unittest.mock import MagicMock

from src.integrations.canvas_lti import CanvasLTIService

CONTEXT_CLAIM = "https://purl.imsglobal.org/spec/lti/claim/context"
CUSTOM_CLAIM = "https://purl.imsglobal.org/spec/lti/claim/custom"
ROLES_CLAIM = "https://purl.imsglobal.org/spec/lti/claim/roles"
PLACEMENT_CLAIM = "https://www.instructure.com/placement"

# Canvas's context claim `id` is an opaque, non-numeric per-deployment
# identifier — deliberately shaped nothing like a Canvas course id so a
# regression that starts using it again is obvious in a diff.
OPAQUE_CONTEXT_ID = "9f89d2fbe2c1a4b7c9d0"

COURSE_CONTEXT = {
    "id": OPAQUE_CONTEXT_ID,
    "label": "NURS 110",
    "title": "Nursing 110",
    "type": ["http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering"],
}


def _service() -> CanvasLTIService:
    """extract_launch_data() doesn't touch `self` — skip __init__ (which
    wants real config/key files) rather than mock a Canvas LTI config."""
    return CanvasLTIService.__new__(CanvasLTIService)


def _mock_launch(id_token: dict) -> MagicMock:
    launch = MagicMock()
    launch.get_launch_data.return_value = id_token
    return launch


def _course_navigation_payload(**overrides) -> dict:
    payload = {
        "iss": "https://canvas.instructure.com",
        "aud": "10000000000001",
        "sub": "a1b2c3-fake-user-id",
        "name": "Jane Instructor",
        "email": "jane@example.edu",
        "nonce": "abc123",
        CONTEXT_CLAIM: COURSE_CONTEXT,
        ROLES_CLAIM: ["http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"],
        PLACEMENT_CLAIM: "course_navigation",
    }
    payload.update(overrides)
    return payload


class TestRealisticCustomClaimExtraction:
    def test_substituted_custom_var_resolves_numeric_course_id(self):
        payload = _course_navigation_payload(
            **{
                CUSTOM_CLAIM: {
                    "canvas_course_id": "33",
                    "canvas_user_id": "12345",
                    "canvas_user_roles": "Instructor",
                }
            }
        )

        result = _service().extract_launch_data(_mock_launch(payload))

        assert result.course_id == "33"
        assert result.placement == "course_navigation"
        # Surfaced unprefixed, exactly as Canvas sends it under the custom
        # claim — no "custom_" prefixing games.
        assert result.custom_params == {
            "canvas_course_id": "33",
            "canvas_user_id": "12345",
            "canvas_user_roles": "Instructor",
        }

    def test_unsubstituted_custom_var_does_not_leak_opaque_context_id(self):
        # canvas_course_id failed to substitute (e.g. the developer key's
        # custom_fields haven't propagated to this placement), but the
        # context claim IS present and IS a real course. course_id must
        # come back empty, not the opaque context.id.
        payload = _course_navigation_payload(
            **{CUSTOM_CLAIM: {"canvas_course_id": "$Canvas.course.id"}}
        )

        result = _service().extract_launch_data(_mock_launch(payload))

        assert result.course_id == ""
        assert result.course_id != OPAQUE_CONTEXT_ID

    def test_missing_custom_claim_entirely_does_not_leak_opaque_context_id(self):
        # No custom claim in the id_token at all (developer key never
        # configured with canvas_course_id, or Canvas omitted it).
        payload = _course_navigation_payload()
        assert CUSTOM_CLAIM not in payload

        result = _service().extract_launch_data(_mock_launch(payload))

        assert result.course_id == ""
        assert result.course_id != OPAQUE_CONTEXT_ID

    def test_account_navigation_payload_has_no_course_id(self):
        payload = _course_navigation_payload(
            **{
                CONTEXT_CLAIM: {
                    "id": "acct-ctx-abc123",
                    "type": ["http://purl.imsglobal.org/vocab/lis/v2/account#Account"],
                },
                CUSTOM_CLAIM: {"canvas_course_id": "$Canvas.course.id"},
                PLACEMENT_CLAIM: "account_navigation",
            }
        )

        result = _service().extract_launch_data(_mock_launch(payload))

        assert result.course_id == ""
        assert result.placement == "account_navigation"
