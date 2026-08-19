"""Fail-closed and secret-safe contracts for the release safety scanner."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCANNER = ROOT / "scripts" / "verify_release_safety.py"


def make_repo(tmp_path: Path, content: str = "ordinary release content\n") -> Path:
    repo = tmp_path / "staging-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "fixture.txt").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "fixture.txt"], check=True)
    return repo


def run_scanner(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--path", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_strict_policy_requires_explicit_denylist_before_scanning(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)

    result = run_scanner(repo, "--strict-policy")

    assert result.returncode == 2
    assert result.stderr.strip() == "error: disclosure policy unavailable or invalid"


def test_strict_policy_rejects_missing_file_without_scanning(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    missing = tmp_path / "protected-policy.json"

    result = run_scanner(repo, "--strict-policy", "--denylist", str(missing))

    assert result.returncode == 2
    assert result.stderr.strip() == "error: disclosure policy unavailable or invalid"
    assert "PASS" not in result.stdout


def test_strict_policy_rejects_unreadable_file_generically(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    policy = tmp_path / "protected-policy.json"
    policy.write_text('{"patterns": ["UnreadableSentinelZXQ"]}', encoding="utf-8")
    policy.chmod(0)

    try:
        result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    finally:
        policy.chmod(0o600)

    assert result.returncode == 2
    assert result.stderr.strip() == "error: disclosure policy unavailable or invalid"
    assert "UnreadableSentinelZXQ" not in result.stdout + result.stderr


def test_strict_policy_rejects_invalid_policies_without_disclosure(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    sentinel = "InvalidPolicySentinelZXQ"
    invalid_documents = (
        f'{{"patterns": ["{sentinel}"]',
        "[]",
        "{}",
        '{"patterns": []}',
        '{"patterns": [""]}',
        '{"patterns": [7]}',
        f'{{"patterns": ["{sentinel}", 7]}}',
        f'{{"patterns": ["{sentinel}("]}}',
    )

    for index, document in enumerate(invalid_documents):
        policy = tmp_path / f"invalid-{index}.json"
        policy.write_text(document, encoding="utf-8")

        result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

        assert result.returncode == 2
        assert (
            result.stderr.strip() == "error: disclosure policy unavailable or invalid"
        )
        assert sentinel not in result.stdout + result.stderr


def test_valid_protected_policy_blocks_without_exposing_match(tmp_path: Path) -> None:
    sentinel = "Zqx"
    repo = make_repo(tmp_path, f"private reference: {sentinel}\n")
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    machine = run_scanner(repo, "--strict-policy", "--denylist", str(policy), "--json")

    assert human.returncode == 1
    assert "[named-entity]" in human.stdout
    assert "[REDACTED]" in human.stdout
    assert sentinel not in human.stdout + human.stderr
    assert machine.returncode == 1
    payload = json.loads(machine.stdout)
    assert payload[0]["check"] == "named-entity"
    assert payload[0]["excerpt"] == "[REDACTED]"
    assert sentinel not in machine.stdout + machine.stderr


def test_named_entity_overlap_redacts_every_finding_for_the_line(
    tmp_path: Path,
) -> None:
    protected_credential = "OverlapProtectedCredentialValue" + "123456789"
    overlapping_values = {
        "generic-secret": "token " + '= "' + protected_credential + '"',
        "internal-host": "overlap-protected" + ".aelira.ai",
        "vendor-contact": "overlap-protected" + "@" + "aelira.ai",
    }

    for check, line in overlapping_values.items():
        case_dir = tmp_path / check
        case_dir.mkdir()
        repo = make_repo(case_dir, f"{line}\n")
        policy = case_dir / "protected-policy.json"
        policy.write_text(json.dumps({"patterns": [line]}), encoding="utf-8")

        human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
        machine = run_scanner(
            repo, "--strict-policy", "--denylist", str(policy), "--json"
        )

        assert human.returncode == 1
        assert machine.returncode == 1
        assert f"[{check}]" in human.stdout
        assert "[named-entity]" in human.stdout
        payload = json.loads(machine.stdout)
        line_findings = [item for item in payload if item["line"] == 1]
        assert {item["check"] for item in line_findings} == {check, "named-entity"}
        assert {item["excerpt"] for item in line_findings} == {"[REDACTED]"}
        for unsafe_fragment in (line, line[:12], line[-6:]):
            assert unsafe_fragment not in human.stdout + human.stderr
            assert unsafe_fragment not in machine.stdout + machine.stderr


def test_protected_entity_in_filename_blocks_without_exposing_path(
    tmp_path: Path,
) -> None:
    sentinel = "ProtectedFilenameSentinelZXQ"
    repo = make_repo(tmp_path)
    protected = repo / f"notes-{sentinel}.png"
    protected.write_text("ordinary release content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", protected.name], check=True)
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    machine = run_scanner(repo, "--strict-policy", "--denylist", str(policy), "--json")

    assert human.returncode == 1
    assert "[named-entity]" in human.stdout
    assert "<redacted-path:" in human.stdout
    assert "[REDACTED]" in human.stdout
    assert sentinel not in human.stdout + human.stderr
    assert machine.returncode == 1
    payload = json.loads(machine.stdout)
    finding = next(item for item in payload if item["check"] == "named-entity")
    assert finding["path"].startswith("<redacted-path:")
    assert finding["excerpt"] == "[REDACTED]"
    assert sentinel not in machine.stdout + machine.stderr


def test_protected_filename_redacts_path_for_content_findings(tmp_path: Path) -> None:
    sentinel = "ProtectedMixedFilenameSentinelZXQ"
    credential = "mixedfilenamecredentialvalue123456789"
    repo = make_repo(tmp_path)
    protected = repo / f"notes-{sentinel}.txt"
    protected.write_text(f'token = "{credential}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", protected.name], check=True)
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    machine = run_scanner(repo, "--strict-policy", "--denylist", str(policy), "--json")

    assert human.returncode == 1
    assert "[named-entity]" in human.stdout
    assert "[generic-secret]" in human.stdout
    assert sentinel not in human.stdout + human.stderr
    assert machine.returncode == 1
    payload = json.loads(machine.stdout)
    protected_findings = [
        item for item in payload if item["check"] in {"named-entity", "generic-secret"}
    ]
    assert {item["check"] for item in protected_findings} == {
        "named-entity",
        "generic-secret",
    }
    assert len({item["path"] for item in protected_findings}) == 1
    assert protected_findings[0]["path"].startswith("<redacted-path:")
    assert sentinel not in machine.stdout + machine.stderr


def test_named_entity_finding_cannot_be_allowlisted(tmp_path: Path) -> None:
    sentinel = "NonSuppressibleEntitySentinelZXQ"
    repo = make_repo(tmp_path, f"private reference: {sentinel}\n")
    (repo / ".release-allowlist.json").write_text(
        json.dumps(
            {
                "allow": [
                    {
                        "path": "fixture.txt",
                        "check": "named-entity",
                        "reason": "reviewed fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    machine = run_scanner(repo, "--strict-policy", "--denylist", str(policy), "--json")

    assert human.returncode == 1
    assert "[named-entity]" in human.stdout
    assert "suppressed" not in human.stdout
    assert sentinel not in human.stdout + human.stderr
    assert machine.returncode == 1
    payload = json.loads(machine.stdout)
    assert any(item["check"] == "named-entity" for item in payload)
    assert sentinel not in machine.stdout + machine.stderr


def test_malformed_allowlists_fail_with_one_generic_non_disclosing_error(
    tmp_path: Path,
) -> None:
    sentinel = "MalformedAllowlistProtectedZXQ"
    malformed_documents = (
        f'{{"allow": ["{sentinel}"]',
        json.dumps([sentinel]),
        json.dumps({"allow": {"path": sentinel, "check": sentinel}}),
        json.dumps({"allow": [sentinel]}),
        json.dumps({"allow": [{"path": sentinel, "check": sentinel}]}),
        json.dumps(
            {"allow": [{"path": [], "check": "generic-secret", "reason": sentinel}]}
        ),
    )

    for index, document in enumerate(malformed_documents):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        repo = make_repo(case_dir)
        allowlist = repo / ".release-allowlist.json"
        allowlist.write_text(document, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", allowlist.name], check=True)
        policy = case_dir / "protected-policy.json"
        policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

        result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

        assert result.returncode == 2
        assert (
            result.stderr.strip()
            == "error: invalid .release-allowlist.json configuration"
        )
        assert result.stdout == ""
        assert sentinel not in result.stdout + result.stderr


def test_tracked_scanner_path_is_scanned_and_secret_is_redacted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "fixturecredentialvalue123456789"
    scanner_fixture = repo / "scripts" / "verify_release_safety.py"
    scanner_fixture.parent.mkdir()
    scanner_fixture.write_text(f'token = "{credential}"\n', encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "scripts/verify_release_safety.py"],
        check=True,
    )

    result = run_scanner(repo, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    finding = next(
        item for item in payload if item["path"] == "scripts/verify_release_safety.py"
    )
    assert finding["check"] == "generic-secret"
    assert finding["excerpt"] != credential
    assert credential not in result.stdout + result.stderr


def test_scans_staged_blob_instead_of_clean_worktree_copy(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "stagedcredentialvalue123456789"
    fixture = repo / "fixture.txt"
    fixture.write_text(f'token = "{credential}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "fixture.txt"], check=True)
    fixture.write_text("ordinary release content\n", encoding="utf-8")

    result = run_scanner(repo, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload[0]["path"] == "fixture.txt"
    assert payload[0]["check"] == "generic-secret"
    assert credential not in result.stdout + result.stderr


def test_staged_empty_allowlist_ignores_worktree_suppression(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "stagedemptyallowlistcredential123456789"
    fixture = repo / "fixture.txt"
    fixture.write_text(f'token = "{credential}"\n', encoding="utf-8")
    allowlist = repo / ".release-allowlist.json"
    allowlist.write_text(json.dumps({"allow": []}), encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "fixture.txt", allowlist.name], check=True
    )
    allowlist.write_text(
        json.dumps(
            {
                "allow": [
                    {
                        "path": "fixture.txt",
                        "check": "generic-secret",
                        "reason": "worktree-only suppression",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_scanner(repo, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any(item["check"] == "generic-secret" for item in payload)
    assert credential not in result.stdout + result.stderr


def test_staged_allowlist_applies_when_worktree_copy_is_empty(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "stagedsuppressioncredentialvalue123456789"
    fixture = repo / "fixture.txt"
    fixture.write_text(f'token = "{credential}"\n', encoding="utf-8")
    allowlist = repo / ".release-allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "allow": [
                    {
                        "path": "fixture.txt",
                        "check": "generic-secret",
                        "reason": "reviewed staged fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "fixture.txt", allowlist.name], check=True
    )
    allowlist.write_text(json.dumps({"allow": []}), encoding="utf-8")

    result = run_scanner(repo, "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout) == []
    assert credential not in result.stdout + result.stderr


def test_untracked_allowlist_is_ignored_as_no_staged_exceptions(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "untrackedallowlistcredentialvalue123456789"
    fixture = repo / "fixture.txt"
    fixture.write_text(f'token = "{credential}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "fixture.txt"], check=True)
    (repo / ".release-allowlist.json").write_text(
        json.dumps(
            {
                "allow": [
                    {
                        "path": "fixture.txt",
                        "check": "generic-secret",
                        "reason": "untracked suppression",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_scanner(repo, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any(item["check"] == "generic-secret" for item in payload)
    assert credential not in result.stdout + result.stderr


def test_tracked_allowlist_blob_retrieval_failure_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    allowlist = repo / ".release-allowlist.json"
    allowlist.write_text(json.dumps({"allow": []}), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", allowlist.name], check=True)
    oid = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ":.release-allowlist.json"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    policy = tmp_path / "protected-policy.json"
    policy.write_text(
        json.dumps({"patterns": ["AbsentProtectedEntityZXQ"]}), encoding="utf-8"
    )

    result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

    assert result.returncode == 2
    assert result.stderr.strip() == "error: unable to read tracked git data"
    assert result.stdout == ""


def test_resolving_symlink_scans_link_blob_not_destination_file(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "destinationcredential123456789"
    target = repo / "untracked-target.txt"
    target.write_text(f'token = "{credential}"\n', encoding="utf-8")
    link = repo / "tracked-link"
    link.symlink_to(target.name)
    subprocess.run(["git", "-C", str(repo), "add", "tracked-link"], check=True)

    result = run_scanner(repo)

    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_dangling_symlink_destination_string_is_scanned(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    link = repo / "tracked-link"
    link.symlink_to("private" + ".aelira.ai")
    subprocess.run(["git", "-C", str(repo), "add", "tracked-link"], check=True)

    result = run_scanner(repo, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload[0]["path"] == "tracked-link"
    assert payload[0]["check"] == "internal-host"


def test_binary_suffixed_symlink_blob_is_scanned_in_strict_mode(tmp_path: Path) -> None:
    sentinel = "BinarySymlinkProtectedZXQ"
    repo = make_repo(tmp_path)
    link = repo / "protected-target.png"
    link.symlink_to(sentinel)
    subprocess.run(["git", "-C", str(repo), "add", link.name], check=True)
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    machine = run_scanner(repo, "--strict-policy", "--denylist", str(policy), "--json")

    assert human.returncode == 1
    assert machine.returncode == 1
    assert "[named-entity]" in human.stdout
    payload = json.loads(machine.stdout)
    assert any(
        item["path"] == link.name
        and item["check"] == "named-entity"
        and item["excerpt"] == "[REDACTED]"
        for item in payload
    )
    assert sentinel not in human.stdout + human.stderr
    assert sentinel not in machine.stdout + machine.stderr


def test_regular_binary_suffix_blobs_are_scanned_for_all_patterns(
    tmp_path: Path,
) -> None:
    sentinel = "RegularBinaryProtectedZXQ"
    credential = "regularbinarycredentialvalue123456789"
    repo = make_repo(tmp_path)
    (repo / "protected.png").write_bytes(
        b"\x89PNG\r\n\x1a\nprivate reference: " + sentinel.encode() + b"\n"
    )
    (repo / "credential.pdf").write_bytes(
        b"%PDF-1.7\n" + f'token = "{credential}"\n'.encode()
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "protected.png", "credential.pdf"],
        check=True,
    )
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    human = run_scanner(repo, "--strict-policy", "--denylist", str(policy))
    machine = run_scanner(repo, "--strict-policy", "--denylist", str(policy), "--json")

    assert human.returncode == 1
    assert "[named-entity]" in human.stdout
    assert "[generic-secret]" in human.stdout
    assert "[REDACTED]" in human.stdout
    assert sentinel not in human.stdout + human.stderr
    assert credential not in human.stdout + human.stderr
    assert machine.returncode == 1
    payload = json.loads(machine.stdout)
    protected = next(item for item in payload if item["path"] == "protected.png")
    built_in = next(item for item in payload if item["path"] == "credential.pdf")
    assert protected["check"] == "named-entity"
    assert protected["excerpt"] == "[REDACTED]"
    assert built_in["check"] == "generic-secret"
    assert built_in["excerpt"] != credential
    assert sentinel not in machine.stdout + machine.stderr
    assert credential not in machine.stdout + machine.stderr


def test_opaque_regular_binary_blob_without_decodable_match_passes(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    (repo / "opaque.png").write_bytes(bytes(range(256)))
    subprocess.run(["git", "-C", str(repo), "add", "opaque.png"], check=True)

    result = run_scanner(repo, "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_nul_delimited_tracked_filename_is_scanned_safely(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    credential = "filenamecredentialvalue123456789"
    unusual = repo / "line\nbreak.txt"
    unusual.write_text(f'token = "{credential}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", unusual.name], check=True)

    result = run_scanner(repo, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload[0]["path"] == unusual.name
    assert payload[0]["check"] == "generic-secret"


def test_suppression_report_never_echoes_allowlist_values_or_controls(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    credential = "controlfilenamecredentialvalue123456789"
    unusual_name = "line\nbreak-\x1b[31m.txt"
    unusual = repo / unusual_name
    unusual.write_text(f'token = "{credential}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", unusual_name], check=True)

    finding_result = run_scanner(repo)

    assert finding_result.returncode == 1
    assert json.dumps(unusual_name) in finding_result.stdout
    assert "\x1b" not in finding_result.stdout + finding_result.stderr

    sentinel = "SuppressionReasonProtectedZXQ"
    reason = f"reviewed {sentinel}\n\x1b[32mfixture"
    allowlist = repo / ".release-allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "allow": [
                    {
                        "path": unusual_name,
                        "check": "generic-secret",
                        "reason": reason,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", allowlist.name], check=True)
    policy = tmp_path / "protected-policy.json"
    policy.write_text(json.dumps({"patterns": [sentinel]}), encoding="utf-8")

    suppressed_result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

    assert suppressed_result.returncode == 1  # named entities are never allowlistable
    assert "suppressed 1 allowlisted finding(s)" in suppressed_result.stdout
    assert ".release-allowlist.json" in suppressed_result.stdout
    assert json.dumps(unusual_name) not in suppressed_result.stdout
    assert json.dumps(reason) not in suppressed_result.stdout
    assert sentinel not in suppressed_result.stdout + suppressed_result.stderr
    assert "\x1b" not in suppressed_result.stdout + suppressed_result.stderr


def test_missing_index_blob_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    policy = tmp_path / "protected-policy.json"
    policy.write_text(
        json.dumps({"patterns": ["AbsentProtectedEntityZXQ"]}), encoding="utf-8"
    )
    oid = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ":fixture.txt"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    object_path = repo / ".git" / "objects" / oid[:2] / oid[2:]
    object_path.unlink()

    result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

    assert result.returncode == 2
    assert result.stderr.strip() == "error: unable to read tracked git data"
    assert "PASS" not in result.stdout


def test_unsupported_git_index_mode_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit_oid = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{commit_oid},unsupported-entry",
        ],
        check=True,
    )

    policy = tmp_path / "protected-policy.json"
    policy.write_text(
        json.dumps({"patterns": ["AbsentProtectedEntityZXQ"]}), encoding="utf-8"
    )

    result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

    assert result.returncode == 2
    assert result.stderr.strip() == (
        "error: git index contains an unsupported entry mode"
    )
    assert "PASS" not in result.stdout


def test_clean_repo_passes_with_valid_policy_outside_repo(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    policy_dir = tmp_path / "runner-temp"
    policy_dir.mkdir()
    policy = policy_dir / "protected-policy.json"
    policy.write_text(
        json.dumps({"patterns": ["AbsentProtectedEntityZXQ"]}), encoding="utf-8"
    )

    result = run_scanner(repo, "--strict-policy", "--denylist", str(policy))

    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_default_local_mode_remains_usable_without_policy(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    result = run_scanner(repo)

    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "no patterns" in result.stderr
