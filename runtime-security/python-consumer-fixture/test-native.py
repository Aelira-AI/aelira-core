"""Benign reader-boundary controls for official metadata and verifier output."""

from __future__ import annotations

import json

from native import BASE_KEYRING_SHA256, validate_baseline, wheel_candidates


def refuses(fn) -> bool:
    try:
        fn()
    except (ValueError, KeyError, TypeError):
        return True
    raise AssertionError("malformed reader input was accepted")


def main() -> None:
    wheel = {
        "packagetype": "bdist_wheel",
        "filename": "package-1-py3-none-any.whl",
        "digests": {"sha256": "a" * 64},
    }
    metadata = {"urls": [wheel]}
    assert wheel_candidates(json.dumps(metadata).encode()) == {
        wheel["filename"]: "a" * 64
    }
    checks = {
        "metadata_duplicate_json_key": refuses(
            lambda: wheel_candidates(b'{"urls":[],"urls":[]}')
        ),
        "metadata_duplicate_wheel": refuses(
            lambda: wheel_candidates(json.dumps({"urls": [wheel, wheel]}).encode())
        ),
        "metadata_bad_digest": refuses(
            lambda: wheel_candidates(
                json.dumps({"urls": [{**wheel, "digests": {"sha256": "bad"}}]}).encode()
            )
        ),
        "metadata_bad_nested_type": refuses(
            lambda: wheel_candidates(b'{"urls":[null]}')
        ),
        "metadata_nonstandard_constant": refuses(
            lambda: wheel_candidates(b'{"urls":NaN}')
        ),
    }
    modules = {
        "src.education.remediation.pdf_remediator": "b" * 64,
        "src.education.handwritten_math_suitability": "c" * 64,
    }
    libraries = {"example.so": {"location": "wheel-private", "sha256": "d" * 64}}
    baseline = {
        "passed": True,
        "state": "official-wheel-before-replacement",
        "selected_public_pins": 38,
        "record_entries": {"pillow": 141, "psycopg2-binary": 35},
        "private_library_dirs_present": ["pillow.libs", "psycopg2_binary.libs"],
        "loaded_modules_sha256": modules,
        "loaded_libraries": {"pillow": libraries, "psycopg2": libraries},
        "apt_versions": {
            name: "1.0"
            for name in (
                "libpq5",
                "libssl3t64",
                "libtiff6",
                "libavif16",
                "libraqm0",
                "fonts-urw-base35",
            )
        },
        "ubuntu_archive_keyring_sha256": BASE_KEYRING_SHA256,
        "pdf_optional_ocr_import": False,
    }
    raw = json.dumps(baseline).encode()
    assert validate_baseline(raw, modules, 38) == baseline
    duplicate = raw.replace(b'"passed": true', b'"passed": true, "passed": true', 1)
    checks["verifier_duplicate_json_key"] = refuses(
        lambda: validate_baseline(duplicate, modules, 38)
    )
    changed = dict(
        baseline,
        loaded_modules_sha256={
            **modules,
            "src.education.remediation.pdf_remediator": "0" * 64,
        },
    )
    checks["verifier_foreign_module"] = refuses(
        lambda: validate_baseline(json.dumps(changed).encode(), modules, 38)
    )
    changed = dict(baseline, loaded_libraries={"pillow": {}, "psycopg2": libraries})
    checks["verifier_missing_private_link"] = refuses(
        lambda: validate_baseline(json.dumps(changed).encode(), modules, 38)
    )
    changed = dict(baseline, selected_public_pins="38")
    checks["verifier_wrong_pin_type"] = refuses(
        lambda: validate_baseline(json.dumps(changed).encode(), modules, 38)
    )
    print(
        json.dumps({"passed": all(checks.values()), "controls": checks}, sort_keys=True)
    )


if __name__ == "__main__":
    main()
