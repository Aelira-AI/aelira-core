"""Caller-supplied Graph IDs, drive paths and item names stay inside their segment."""

from urllib.parse import quote

import httpx
import pytest
from pydantic import ValidationError

from src.api.integration_routes import AddSyncFolderRequest
from src.api.microsoft_routes import CreateSubscriptionRequest
from src.integrations.microsoft_365.onedrive import (
    GraphPathRejected,
    OneDriveIntegration,
    graph_drive_path,
    graph_id,
    graph_name,
)

# The ID shapes Microsoft documents for drive items (business and personal),
# drives, sites and subscriptions, plus the "root" alias.
DOCUMENTED_IDS = [
    "root",
    "01BYE5RZ6QN3ZWBTUFOFD3GSPGOHDJD36K",
    "D4648F06C91D9D3D!54927",
    "F3B6CBA7A4DB2C69!s1b6f6d4c3c6e4b8a9e4c4b1f0b8f8e8f",
    "d4648f06c91d9d3d",
    "b!-RIj2DuyvEyV1T4NlOaMHk8XkS_I8MdFlUCq1BlcjgmhRfAj3-Z8RY2VpuvV_tpd",
    "contoso.sharepoint.com,2C712604-1370-44E7-A1F5-426573FDA80A,"
    "2D2244C3-251A-49EA-93A8-39E1C3A060FE",
    "7f103cc7-7a4b-4fa7-a3e8-2e5d1a8e5b35",
]

UNSAFE_IDS = [
    "",
    ".",
    "..",
    "item..",
    "../me",
    "item/children",
    "item?$select=id",
    "item#fragment",
    "item%2F..",
    "item\\..",
    "item id",
    "item:/x:",
    "item\n",
    "a" * 513,
    None,
    7,
]


@pytest.mark.parametrize("value", DOCUMENTED_IDS)
def test_documented_graph_ids_pass_unchanged(value):
    assert graph_id(value) == value


@pytest.mark.parametrize("value", UNSAFE_IDS)
def test_other_ids_are_refused(value):
    with pytest.raises(GraphPathRejected):
        graph_id(value)


@pytest.mark.parametrize(
    "path,encoded",
    [
        ("Course A/Week 1", "Course%20A/Week%201"),
        ("/Course//Week/", "Course/Week"),
        ("Notes: draft?#100%", "Notes%3A%20draft%3F%23100%25"),
        (".config/v1.0", ".config/v1.0"),
        ("", ""),
        ("/", ""),
    ],
)
def test_drive_paths_are_encoded_segment_by_segment(path, encoded):
    assert graph_drive_path(path) == encoded


@pytest.mark.parametrize(
    "path",
    [
        "Course/../other",
        "./Course",
        "Course/..",
        "Course/...",
        "Course/.. ",
        "\u3000../other",
        "\uff0e\uff0e/other",
        "a\\b",
        "Course\x00",
        "Course\x85",
        "Course\ud800",
    ],
)
def test_dot_segments_and_unsafe_characters_in_paths_are_refused(path):
    with pytest.raises(GraphPathRejected):
        graph_drive_path(path)


def test_item_names_are_encoded_as_one_segment():
    assert graph_name("Week 1#draft?.pdf") == "Week%201%23draft%3F.pdf"


# Real names: an ideographic space, a ZWJ emoji, a no-break space, a soft
# hyphen and a left-to-right mark are all ordinary characters to encode.
@pytest.mark.parametrize(
    "name",
    [
        "\u5831\u544a\u66f8\u3000\u6848.docx",
        "Team \U0001f468\u200d\U0001f469\u200d\U0001f467.docx",
        "x\u00a0y.pdf",
        "co\u00adop.docx",
        "a\u200eb.pdf",
    ],
)
def test_names_with_non_ascii_spaces_and_marks_are_encoded(name):
    assert graph_name(name) == quote(name, safe="")
    assert graph_drive_path(f"Course/{name}") == "Course/" + quote(name, safe="")


@pytest.mark.parametrize("name", ["", ".", "..", "a/b.pdf", "../b.pdf", "a\\b.pdf"])
def test_item_names_that_are_not_one_segment_are_refused(name):
    with pytest.raises(GraphPathRejected):
        graph_name(name)


def test_microsoft_sync_folder_must_be_a_graph_id():
    with pytest.raises(ValidationError):
        AddSyncFolderRequest(
            provider="microsoft", folder_id="folder-1/../x", folder_name="Course"
        )
    assert (
        AddSyncFolderRequest(
            provider="microsoft", folder_id="01BYE5RZ6QN3", folder_name="Course"
        ).folder_id
        == "01BYE5RZ6QN3"
    )
    # Google folder IDs are not Graph IDs and keep their own handling.
    assert AddSyncFolderRequest(
        provider="google", folder_id="0B-x_y", folder_name="Course"
    )
    # The stored column holds 255 characters.
    with pytest.raises(ValidationError):
        AddSyncFolderRequest(provider="google", folder_id="x" * 256, folder_name="C")


@pytest.mark.parametrize(
    "resource",
    [
        "/drives/../root",
        "/sites/..%2Fme/drive/root",
        "/drives/%2E%2E/root",
        "/sites/a..b/drive/root",
    ],
)
def test_subscription_resource_ids_must_be_graph_ids(resource):
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest(resource=resource)


def test_documented_subscription_resources_are_accepted():
    for resource in (
        "/me/drive/root",
        f"/drives/{DOCUMENTED_IDS[5]}/root",
        f"/sites/{DOCUMENTED_IDS[6]}/drive/root",
    ):
        assert CreateSubscriptionRequest(resource=resource).resource == resource


def _integration(requests, **kwargs):
    def record(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "new-1", "value": []})

    integration = OneDriveIntegration(
        access_token="graph-token", department_id="d", **kwargs
    )
    integration._http_client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda i: i.list_files(folder_id=".."),
        lambda i: i.list_folders(parent_folder_id="folder-1/children?"),
        lambda i: i.get_file_info("file-1#x"),
        lambda i: i.get_delta(folder_id="../root"),
        lambda i: i.delete_webhook("../sub"),
        lambda i: i.renew_webhook("sub-1?x"),
    ],
)
async def test_adapter_refuses_unsafe_ids_before_any_request(call):
    requests = []
    integration = _integration(requests)
    try:
        with pytest.raises(GraphPathRejected):
            await call(integration)
    finally:
        await integration.close()
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"drive_id": "drive-1/.."}, {"site_id": "s?x"}])
async def test_adapter_refuses_an_unsafe_drive_or_site(kwargs):
    requests = []
    integration = _integration(requests, **kwargs)
    try:
        with pytest.raises(GraphPathRejected):
            await integration.list_files()
        with pytest.raises(GraphPathRejected):
            await integration.create_webhook(
                "https://example.test/hook", client_state="s" * 43
            )
    finally:
        await integration.close()
    assert requests == []


@pytest.mark.asyncio
async def test_download_of_an_unsafe_id_fails_without_a_request(tmp_path):
    requests = []
    integration = _integration(requests)
    try:
        result = await integration.download_file("..", str(tmp_path / "out"))
    finally:
        await integration.close()
    assert not result.success
    assert requests == []


@pytest.mark.asyncio
async def test_upload_name_is_encoded_into_its_own_segment(tmp_path):
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"%PDF-test")
    requests = []
    integration = _integration(requests)
    try:
        result = await integration.upload_file(
            str(source), "folder-1", "Week 1#draft?.pdf"
        )
    finally:
        await integration.close()
    assert result.success
    (request,) = requests
    assert (
        request.url.raw_path
        == b"/v1.0/me/drive/items/folder-1:/Week%201%23draft%3F.pdf:/content"
    )


# Both the simple (< 4 MB) and the resumable upload paths are covered.
@pytest.mark.asyncio
@pytest.mark.parametrize("size", [9, 4 * 1024 * 1024])
@pytest.mark.parametrize(
    "folder_id,name,kwargs",
    [
        (None, "../other.pdf", {}),
        ("folder-1/..", "a.pdf", {}),
        (None, "a.pdf", {"drive_id": "drive-1?x"}),
    ],
)
async def test_upload_refusal_is_a_known_outcome(
    tmp_path, size, folder_id, name, kwargs
):
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"x" * size)
    requests = []
    integration = _integration(requests, **kwargs)
    try:
        result = await integration.upload_file(str(source), folder_id, name)
    finally:
        await integration.close()
    assert not result.success
    assert result.error == "invalid_graph_reference"
    # Nothing was sent, so the job must not be held as possibly applied.
    assert result.failure_kind == "deterministic"
    assert requests == []
