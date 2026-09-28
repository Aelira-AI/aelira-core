"""PDF edit labels come from verified structure ownership, never text matching."""

import hashlib
import json

import pytest
from pikepdf import Dictionary, Name, String

from src.education import pdf_review_candidate as edit
from src.education import reading_order_snapshot as snapshot
from test_pdf_review_candidate import rewrite, table_pdf
from test_reading_order_snapshot import make_pdf
from test_review_pdf_edit_routes import _configure

pytestmark = pytest.mark.unit
pytest_plugins = ["test_review_reading_order"]


def inspect(content):
    return edit.inspect_pdf_edit_targets(content, hashlib.sha256(content).hexdigest())


def test_repeated_text_has_distinct_owners_and_verified_pages():
    source = make_pdf(pages=2, duplicate=True)
    paragraphs = [target for target in inspect(source) if target["role"] == "P"]
    assert len(paragraphs) == 4
    assert len({target["target_id"] for target in paragraphs}) == 4
    assert [target["context"]["page_numbers"] for target in paragraphs] == [
        [1],
        [1],
        [2],
        [2],
    ]
    assert all(
        target["context"]["segments"]
        == [
            {
                "page_number": target["context"]["page_numbers"][0],
                "text": "Repeated",
                "source": "MCID",
            }
        ]
        for target in paragraphs
    )
    root = inspect(source)[0]
    assert root["context"]["page_numbers"] == [1, 2]
    assert [segment["page_number"] for segment in root["context"]["segments"]] == [
        1,
        1,
        2,
        2,
    ]


def test_nested_table_context_excludes_sibling_text(tmp_path):
    source = table_pdf(tmp_path)
    targets = inspect(source)
    table = next(target for target in targets if target["role"] == "Table")
    cells = [target for target in targets if target["role"] == "TD"]
    assert table["context"]["page_numbers"] == [1]
    table_text = [segment["text"] for segment in table["context"]["segments"]]
    assert any("North entry" in text for text in table_text)
    assert any("South entry" in text for text in table_text)
    assert not any("Heading" in text or "Closing" in text for text in table_text)
    assert all(cell["context"]["page_numbers"] == [1] for cell in cells)
    assert len({cell["target_id"] for cell in cells}) == len(cells)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"replacement": " Exact replacement "}, (" Exact replacement ", "ActualText")),
        (
            {"marked_actual": " marked phrase ", "kind": "mcr"},
            (" marked phrase ", "ActualText"),
        ),
        ({"alt": " Figure description "}, (" Figure description ", "Alt")),
    ],
)
def test_alternative_sources_keep_their_semantic_type(kwargs, expected):
    source = make_pdf(**kwargs)
    paragraphs = [target for target in inspect(source) if target["role"] == "P"]
    matching = [
        segment
        for target in paragraphs
        for segment in target["context"]["segments"]
        if segment["source"] == expected[1]
    ]
    assert matching
    assert matching[0] == {"page_number": 1, "text": expected[0], "source": expected[1]}
    if expected[1] == "Alt":
        owner = next(
            target
            for target in paragraphs
            if any(
                segment["source"] == "Alt" for segment in target["context"]["segments"]
            )
        )
        assert [segment["source"] for segment in owner["context"]["segments"]] == [
            "Alt",
            "MCID",
        ]


def test_nested_parent_uses_only_descendants_and_candidate_ids_stay_stable():
    def nest(pdf):
        root = pdf.Root.StructTreeRoot
        first = root.K[0]
        section = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Sect, P=root, K=first)
        )
        first.P = section
        root.K[0] = section

    source = rewrite(make_pdf(), nest)
    targets = inspect(source)
    section = next(target for target in targets if target["role"] == "Sect")
    child = next(
        target for target in targets if target["target_id"] in section["children"]
    )
    assert section["context"] == child["context"]
    assert section["context"]["segments"][0]["text"] == "Second page 0"
    assert "First page 0" not in json.dumps(section["context"])
    assert section["target_id"].endswith(":0")
    operation = {"kind": "heading", "target_id": child["target_id"], "level": 2}
    result = edit.create_pdf_edit_candidate(
        source, hashlib.sha256(source).hexdigest(), operation
    )
    assert result.operation == "heading"


def test_context_limits_mark_truncation_without_inventing_pages(monkeypatch):
    source = make_pdf(pages=17, replacement="é" * 300)
    targets = inspect(source)
    root = targets[0]["context"]
    assert root["page_numbers"] == list(range(1, 17))
    assert root["truncated"]
    assert len(root["segments"]) <= edit.MAX_CONTEXT_SEGMENTS
    assert (
        sum(len(segment["text"]) for segment in root["segments"])
        <= edit.MAX_CONTEXT_TEXT_CHARACTERS
    )
    assert all(
        segment["page_number"] in root["page_numbers"] for segment in root["segments"]
    )
    replacement = next(
        target
        for target in targets
        if target["role"] == "P"
        and target["context"]["segments"][0]["source"] == "ActualText"
    )
    assert replacement["context"]["segments"][0]["text"] == "é" * 240
    assert replacement["context"]["truncated"]

    fallback = {
        "status": "unavailable",
        "page_numbers": [],
        "segments": [],
        "truncated": True,
    }
    fallback_size = len(json.dumps(fallback, separators=(",", ":")).encode())
    monkeypatch.setattr(edit, "MAX_CONTEXT_TOTAL_BYTES", fallback_size * len(targets))
    limited = inspect(source)
    assert (
        sum(
            len(
                json.dumps(
                    target["context"], ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            for target in limited
        )
        <= edit.MAX_CONTEXT_TOTAL_BYTES
    )
    omitted = [target["context"] for target in limited if target["context"] == fallback]
    assert len(omitted) >= 2
    omitted[0]["page_numbers"].append(99)
    assert omitted[1]["page_numbers"] == []


def test_empty_actual_text_keeps_verified_page_without_fallback_text():
    paragraphs = [
        target
        for target in inspect(make_pdf(replacement=" \n\t "))
        if target["role"] == "P"
    ]
    empty = next(target for target in paragraphs if not target["context"]["segments"])
    assert empty["context"] == {
        "status": "empty",
        "page_numbers": [1],
        "segments": [],
        "truncated": False,
    }


def test_segment_limit_is_independent_of_text_limit():
    root = inspect(make_pdf(pages=3))[0]["context"]
    assert root["page_numbers"] == [1, 2, 3]
    assert len(root["segments"]) == edit.MAX_CONTEXT_SEGMENTS
    assert (
        sum(len(segment["text"]) for segment in root["segments"])
        < edit.MAX_CONTEXT_TEXT_CHARACTERS
    )
    assert root["truncated"]


def test_global_budget_counts_utf8_bytes_not_characters(monkeypatch):
    source = make_pdf(replacement="é" * 100)
    contexts = [target["context"] for target in inspect(source)]
    serialized = [
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in contexts
    ]
    character_budget = sum(len(item) for item in serialized)
    assert sum(len(item.encode()) for item in serialized) > character_budget
    monkeypatch.setattr(edit, "MAX_CONTEXT_TOTAL_BYTES", character_budget)
    limited = [target["context"] for target in inspect(source)]
    assert (
        sum(
            len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
            for item in limited
        )
        <= character_budget
    )
    assert any(
        item["status"] == "unavailable" and item["truncated"] for item in limited
    )


def test_malformed_owner_and_parent_alternative_refuse_context():
    with pytest.raises(edit.PDFEditRefused):
        inspect(make_pdf(defect="owner"))

    def mask_parent(pdf):
        root = pdf.Root.StructTreeRoot
        parent = pdf.make_indirect(
            Dictionary(
                Type=Name.StructElem,
                S=Name.Sect,
                P=root,
                K=root.K,
                ActualText=String("not descendant text"),
            )
        )
        for child in parent.K:
            child.P = parent
        root.K = parent

    with pytest.raises(edit.PDFEditRefused, match="parent_text_alternative"):
        inspect(rewrite(make_pdf(), mask_parent))


def test_inspection_route_passes_context_unchanged_and_rejects_label_injection(preview):
    expected = _configure(preview)
    direct = inspect(preview.source_bytes)
    url = preview.url.replace("reading-order", "pdf-edit-targets")
    response = preview.client.get(url)
    assert response.status_code == 200
    assert response.json()["targets"] == direct
    target = next(item for item in direct if item["can_set_heading"])
    payload = {
        "source_kind": "original",
        "cloud_file_id": None,
        "expected_artifact_id": expected["expected_artifact_id"],
        "expected_source_sha256": expected["source_sha256"],
        "expected_state_digest": expected["state_digest"],
        "operation": {
            "kind": "heading",
            "target_id": target["target_id"],
            "level": 2,
            "context": {"segments": [{"text": "injected"}]},
        },
    }
    assert (
        preview.client.post(
            url.replace("pdf-edit-targets", "pdf-edit-candidates"), json=payload
        ).status_code
        == 422
    )
    preview.service.claim_and_publish_stream.assert_not_called()
    before = snapshot.inspect_pdf_reading_order(preview.source_bytes, 1)
    assert set(before) == {
        "status",
        "reason",
        "sha256",
        "page_count",
        "page_number",
        "width",
        "height",
        "preview_png_base64",
        "blocks",
        "unpositioned_count",
    }
