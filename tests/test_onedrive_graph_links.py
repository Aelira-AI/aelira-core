"""OneDrive follows only Microsoft Graph v1.0 paging and delta links."""

import httpx
import pytest

from src.integrations.microsoft_365.onedrive import (
    GraphLinkRejected,
    OneDriveIntegration,
    require_graph_link,
)

GOOD = "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=abc"
NON_GRAPH = [
    "http://graph.microsoft.com/v1.0/me/drive/root/delta",
    "https://other.example/v1.0/me/drive/root/delta",
    "https://graph.microsoft.com@other.example/v1.0/me",
    "https://user@graph.microsoft.com/v1.0/me",
    "https://user:pass@graph.microsoft.com/v1.0/me",
    "https://graph.microsoft.com.other.example/v1.0/me",
    "https://graph.microsoft.com./v1.0/me",
    "https://graph.microsoft.com:444/v1.0/me",
    "https://graph.microsoft.com:notaport/v1.0/me",
    "https://graph.microsoft.com/beta/me",
    "https://graph.microsoft.com/v1.0/../beta/me",
    "https://graph.microsoft.com/v1.0/me/..",
    "https://graph.microsoft.com/v1.0/me#fragment",
    "https://graph.microsoft.com/v1.0/me\nHost: other.example",
    "https://graph.microsoft.com\\@other.example/v1.0/me",
    "//other.example/v1.0/me",
    "not a url",
    "",
    None,
    7,
]


def test_graph_v1_links_are_accepted():
    assert require_graph_link(GOOD) == GOOD
    assert require_graph_link("https://GRAPH.microsoft.com:443/v1.0/me") is not None


@pytest.mark.parametrize("link", NON_GRAPH)
def test_other_links_are_refused(link):
    with pytest.raises(GraphLinkRejected):
        require_graph_link(link)


def _integration(handler, requests):
    def record(request):
        requests.append(request)
        return handler(request)

    integration = OneDriveIntegration(access_token="graph-token", department_id="d")
    integration._http_client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return integration


def _delta_page(request):
    if "page2" in str(request.url):
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": GOOD})
    return httpx.Response(
        200,
        json={
            "value": [
                {"id": "f1", "name": "a.pdf", "file": {"mimeType": "application/pdf"}}
            ],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?page2",
        },
    )


@pytest.mark.asyncio
async def test_delta_pages_through_graph_links():
    requests = []
    integration = _integration(_delta_page, requests)
    try:
        files, delta = await integration.get_delta(delta_token=GOOD)
    finally:
        await integration.close()
    assert [item.id for item in files] == ["f1"]
    assert delta == GOOD
    assert len(requests) == 2
    assert all(r.url.host == "graph.microsoft.com" for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("link", NON_GRAPH[:7])
async def test_stored_delta_link_off_graph_is_refused_before_any_request(link):
    requests = []
    integration = _integration(_delta_page, requests)
    try:
        with pytest.raises(GraphLinkRejected):
            await integration.get_delta(delta_token=link)
    finally:
        await integration.close()
    assert requests == []


@pytest.mark.asyncio
async def test_delta_next_link_off_graph_is_not_followed():
    requests = []

    def handler(request):
        return httpx.Response(
            200,
            json={"value": [], "@odata.nextLink": "https://other.example/v1.0/x"},
        )

    integration = _integration(handler, requests)
    try:
        with pytest.raises(GraphLinkRejected):
            await integration.get_delta()
    finally:
        await integration.close()
    assert len(requests) == 1
    assert requests[0].url.host == "graph.microsoft.com"


@pytest.mark.asyncio
async def test_list_files_refuses_a_page_token_off_graph_before_any_request():
    requests = []
    integration = _integration(_delta_page, requests)
    try:
        with pytest.raises(GraphLinkRejected):
            await integration.list_files(page_token="https://other.example/v1.0/x")
    finally:
        await integration.close()
    assert requests == []


@pytest.mark.asyncio
async def test_list_all_files_stops_at_a_next_link_off_graph():
    requests = []

    def handler(request):
        return httpx.Response(
            200,
            json={
                "value": [{"id": "f1", "name": "a.pdf", "file": {}}],
                "@odata.nextLink": "http://graph.microsoft.com/v1.0/me/drive/root/children?page2",
            },
        )

    integration = _integration(handler, requests)
    seen = []
    try:
        with pytest.raises(GraphLinkRejected):
            async for item in integration.list_all_files(recursive=False):
                seen.append(item.id)
    finally:
        await integration.close()
    assert seen == ["f1"]
    assert len(requests) == 1
