"""Canvas Developer Key configuration security contract."""

from src.integrations.canvas_lti import CanvasLTIService


def _config():
    service = CanvasLTIService.__new__(CanvasLTIService)
    return service.generate_lti_config_json("https://aelira.example")


def _placements():
    config = _config()
    return {
        placement["placement"]: placement
        for placement in config["extensions"][0]["settings"]["placements"]
    }


def test_canvas_navigation_placements_are_admin_visible():
    placements = _placements()

    assert placements["course_navigation"]["visibility"] == "admins"
    assert placements["account_navigation"]["visibility"] == "admins"


def test_canvas_deep_link_placements_do_not_claim_navigation_visibility_control():
    placements = _placements()

    assert "visibility" not in placements["assignment_selection"]
    assert "visibility" not in placements["editor_button"]


def test_canvas_custom_field_contract_uses_unprefixed_lti13_claim_members():
    custom_fields = _config()["custom_fields"]

    assert custom_fields["canvas_course_id"] == "$Canvas.course.id"
    assert custom_fields["canvas_resource_link_id"] == "$ResourceLink.id"
    assert "custom_canvas_course_id" not in custom_fields
    assert "custom_canvas_resource_link_id" not in custom_fields
