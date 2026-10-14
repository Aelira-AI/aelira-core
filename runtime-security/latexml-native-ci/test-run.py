#!/usr/bin/env python3
"""Portable local controls; synthetic filesystem/Git checks are not producer proof."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import run as common


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scratch", required=True, type=Path)
    p.add_argument("--operator-repo", required=True, type=Path)
    p.add_argument("--operator-ref", required=True)
    p.add_argument("--operator-tree", required=True)
    p.add_argument("--native-cleanup", action="store_true")
    a = p.parse_args()
    os.umask(0o022)
    a.source_kind = "synthetic"
    a.remote_ref = None
    a.architecture = "arm64"
    common.path_check(a.scratch, new=True)
    a.scratch.mkdir()
    rows = []

    def control(name, fn, expected):
        try:
            fn()
            code = 0
        except (ValueError, OSError, subprocess.TimeoutExpired):
            code = 2
        rows.append({"case": name, "exit": code, "expected_exit": expected})
        common.require(code == expected, "control result differs")

    base = a.scratch / "positive"
    base.mkdir()
    (base / "nested").mkdir()
    (base / "nested/file").write_bytes(b"owned synthetic input\n")
    control(
        "positive-exact-readable-closure",
        lambda: common.inventory(base, {"nested/file"}),
        0,
    )
    # Synthetic transport controls test response/refusal logic, not native TLS.
    original_connection = common.http.client.HTTPSConnection
    original_sleep = common.time.sleep

    class Response:
        def __init__(self, status, data):
            self.status, self.data = status, data

        def read(self, bound):
            return self.data[:bound]

    class Connection:
        response = Response(200, b"known bounded metadata")

        def __init__(self, host, *, timeout, context):
            common.require(
                host == "snapshot.ubuntu.com"
                and timeout == 60
                and context.check_hostname is True
                and context.verify_mode == common.ssl.CERT_REQUIRED,
                "normal snapshot TLS context required",
            )

        def request(self, method, path):
            common.require(
                method == "GET"
                and path
                == "/ubuntu/" + common.SNAPSHOT_ID + "/dists/resolute/InRelease",
                "fixed snapshot resource required",
            )

        def getresponse(self):
            return self.response

        def close(self):
            pass

    try:
        common.http.client.HTTPSConnection = Connection
        common.time.sleep = lambda _: None
        control(
            "snapshot-synthetic-bounded-response-positive",
            lambda: common.require(
                common.snapshot_file("dists/resolute/InRelease", 100)
                == b"known bounded metadata"
            ),
            0,
        )
        for case, status_code, body in (
            ("redirect", 302, b"redirect"),
            ("unavailable", 503, b"unavailable"),
            ("empty", 200, b""),
            ("oversized", 200, b"x" * 101),
        ):
            Connection.response = Response(status_code, body)
            control(
                "snapshot-synthetic-" + case + "-refused",
                lambda: common.snapshot_file("dists/resolute/InRelease", 100),
                2,
            )
        control(
            "snapshot-undeclared-resource-refused-before-transport",
            lambda: common.snapshot_file("arbitrary/source", 100),
            2,
        )

        class SequenceConnection(Connection):
            answers = []
            calls = 0
            closes = 0

            def __init__(self, host, *, timeout, context):
                super().__init__(host, timeout=timeout, context=context)
                self.answer = self.answers[type(self).calls]
                type(self).calls += 1

            def getresponse(self):
                if isinstance(self.answer, BaseException):
                    raise self.answer
                return self.answer

            def close(self):
                type(self).closes += 1

        common.http.client.HTTPSConnection = SequenceConnection
        delays = []
        common.time.sleep = delays.append

        def sequence(name, answers, expected, waits):
            SequenceConnection.answers = answers
            SequenceConnection.calls = SequenceConnection.closes = 0
            delays.clear()
            control(
                "snapshot-retry-" + name,
                lambda: common.require(
                    common.snapshot_file("dists/resolute/InRelease", 100)
                    == b"known bounded metadata"
                ),
                expected,
            )
            control(
                "snapshot-retry-" + name + "-bounded-closed",
                lambda: common.require(
                    SequenceConnection.calls == len(answers)
                    and SequenceConnection.closes == len(answers)
                    and delays == waits
                ),
                0,
            )

        good = Response(200, b"known bounded metadata")
        sequence("http-recovery", [Response(503, b""), good], 0, [2])
        sequence("timeout-recovery", [TimeoutError(), good], 0, [2])
        sequence(
            "http-error-recovery", [common.http.client.HTTPException(), good], 0, [2]
        )
        sequence(
            "all-transient-statuses",
            [Response(s, b"") for s in (500, 502, 503, 504)] + [good],
            0,
            [2, 4, 8, 16],
        )
        sequence("http-exhausted", [Response(503, b"")] * 5, 2, [2, 4, 8, 16])
        sequence("transport-exhausted", [TimeoutError()] * 5, 2, [2, 4, 8, 16])
        sequence("certificate-refused", [common.ssl.SSLCertVerificationError()], 2, [])
        sequence("tls-refused", [common.ssl.SSLError()], 2, [])
        sequence("redirect-refused", [Response(302, b"")], 2, [])
        sequence("not-found-refused", [Response(404, b"")], 2, [])
        sequence("empty-refused", [Response(200, b"")], 2, [])
        sequence("oversized-refused", [Response(200, b"x" * 101)], 2, [])
    finally:
        common.http.client.HTTPSConnection = original_connection
        common.time.sleep = original_sleep
    control(
        "snapshot-empty-CA-archive-refused", lambda: common.snapshot_ca_bundle(b""), 2
    )
    # Exercise cleanup's success path before any protocol/refusal fixture.
    cid = a.scratch / "synthetic-container.cid"
    cid.write_text("a" * 64)
    original_command, original_sleep = common.command, common.time.sleep
    answers, calls = [], []

    def docker_control(args, timeout=60):
        calls.append((args, timeout))
        value = answers.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def cleanup_case(name, sequence, expected, stops):
        answers[:] = sequence
        calls.clear()
        control(
            "capture-cleanup-" + name, lambda: common.cleanup_capture(cid), expected
        )
        control(
            "capture-cleanup-" + name + "-exact-target",
            lambda: common.require(
                sum(args[1] == "stop" for args, _ in calls) == stops
                and all(args[-1] in ("id=" + "a" * 64, "a" * 64) for args, _ in calls)
                and not answers
            ),
            0,
        )

    try:
        common.command = docker_control
        common.time.sleep = lambda _: None
        active = ("a" * 64 + "\n").encode()
        cleanup_case("absent-positive", [b""], 0, 0)
        cleanup_case("active-positive", [active, active, b""], 0, 1)
        cleanup_case("wrong-query-refused", [("b" * 64 + "\n").encode()], 2, 0)
        cleanup_case("query-unavailable", [common.ProcessFailure(1)], 2, 0)
        cleanup_case("stop-unavailable", [active, common.ProcessFailure(1)], 2, 1)
        cleanup_case("retained-container-refused", [active] * 7, 2, 1)
        cid.write_text("a" * 12)
        cleanup_case("invalid-id-before-query", [], 2, 0)
    finally:
        common.command, common.time.sleep = original_command, original_sleep
    if a.native_cleanup:
        common.require(
            sys.platform.startswith("linux") and os.getuid() != 0,
            "native nonroot cleanup control required",
        )
        actual_cid = a.scratch / "actual-container.cid"

        def native_cleanup():
            try:
                subprocess.run(
                    [
                        "/usr/bin/docker",
                        "run",
                        "--rm",
                        "--cidfile",
                        str(actual_cid),
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--user",
                        "1000:1000",
                        "--memory",
                        "64m",
                        "--cpus",
                        "1",
                        "--pids-limit",
                        "16",
                        "--entrypoint",
                        "/bin/sh",
                        common.BASE,
                        "-c",
                        "printf ready; sleep 30",
                    ],
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
                raise ValueError("native timeout control did not time out")
            except subprocess.TimeoutExpired as error:
                try:
                    common.require(
                        error.stdout == b"ready",
                        "native started-container positive missing",
                    )
                finally:
                    common.cleanup_capture(actual_cid)

        control("capture-cleanup-actual-started-timeout", native_cleanup, 0)
    empty = a.scratch / "empty"
    empty.mkdir()
    (empty / "unexpected").mkdir()
    control("extra-empty-directory", lambda: common.inventory(empty, set()), 2)
    symlink = a.scratch / "symlink"
    symlink.mkdir()
    (symlink / "file").symlink_to(base / "nested/file")
    control("symlink-member", lambda: common.inventory(symlink, {"file"}), 2)
    hard = a.scratch / "hardlink"
    hard.mkdir()
    os.link(base / "nested/file", hard / "file")
    control("hardlink-member", lambda: common.inventory(hard, {"file"}), 2)
    inaccessible = a.scratch / "unreadable"
    inaccessible.mkdir()
    (inaccessible / "denied").mkdir()
    (inaccessible / "denied").chmod(0)
    control(
        "unreadable-directory",
        lambda: common.inventory(inaccessible, {"denied/file"}),
        2,
    )
    alias = a.scratch / "alias"
    alias.symlink_to(a.scratch / "positive", target_is_directory=True)
    control("aliased-root", lambda: common.inventory(alias, {"nested/file"}), 2)
    unexpected = a.scratch / "extra"
    unexpected.mkdir()
    (unexpected / "file").write_bytes(b"undeclared text")
    control("undeclared-file", lambda: common.inventory(unexpected, set()), 2)
    receipt = a.scratch / "duplicate.json"
    receipt.write_bytes(b'{"field":1,"field":2}\n')
    control("duplicate-JSON", lambda: common.read_json(receipt), 2)
    nan = a.scratch / "nonfinite.json"
    nan.write_bytes(b'{"field":NaN}\n')
    control("nonfinite-JSON", lambda: common.read_json(nan), 2)
    valid = {n: {"state": "executed", "exit": 0} for n in common.STAGES}
    control(
        "typed-original-stage-positive", lambda: common.validate_stages(valid, True), 0
    )
    false = {n: dict(v) for n, v in valid.items()}
    false["native-run"]["exit"] = False
    control("boolean-stage-exit", lambda: common.validate_stages(false), 2)
    failed = {n: dict(v) for n, v in valid.items()}
    failed["native-run"]["exit"] = 7
    control(
        "original-failure-retained-schema", lambda: common.validate_stages(failed), 0
    )
    control(
        "failed-stage-success-export-refusal",
        lambda: common.validate_stages(failed, True),
        2,
    )
    unavailable = {n: dict(v) for n, v in valid.items()}
    unavailable["native-run"] = {"state": "unavailable", "exit": None}
    control(
        "unavailable-without-fabricated-exit",
        lambda: common.validate_stages(unavailable),
        0,
    )
    unavailable["native-run"]["exit"] = 125
    control(
        "unavailable-status-substitution",
        lambda: common.validate_stages(unavailable),
        2,
    )
    control("selected-Git-source-positive", lambda: common.selected(a), 0)
    wrong = argparse.Namespace(**vars(a))
    wrong.operator_tree = "0" * 40
    control("wrong-selected-tree", lambda: common.selected(wrong), 2)
    wrong = argparse.Namespace(**vars(a))
    wrong.remote_ref = "refs/heads/main"
    control("synthetic-public-ref-refusal", lambda: common.selected(wrong), 2)
    control("existing-output-refusal", lambda: common.path_check(base, new=True), 2)
    oversized = a.scratch / "oversize"
    oversized.write_bytes(b"ab")
    control("bounded-file-size", lambda: common.regular(oversized, 1), 2)
    directory_case = a.scratch / "directory-mode"
    directory_case.mkdir()
    (directory_case / "nested").mkdir()
    (directory_case / "nested/file").write_bytes(b"mode fixture\n")
    control(
        "directory-mode-positive",
        lambda: common.directory_modes(directory_case, {"nested/file"}),
        0,
    )
    (directory_case / "nested").chmod(0o750)
    control(
        "declared-parent-mode-refusal",
        lambda: common.inventory(directory_case, {"nested/file"}),
        2,
    )
    (directory_case / "nested").chmod(0o755)
    directory_case.chmod(0o700)
    control(
        "output-root-mode-refusal",
        lambda: common.inventory(directory_case, {"nested/file"}),
        2,
    )
    # Authenticate the selected caller before exercising only its passive tracker.
    members = common.selected(a)
    caller = common.load_module(
        a.operator_repo / common.CALLER / "native_acceptance.py", "portable_caller"
    )
    expected = common.diagnostic_expectations(a, members, common.ARM_LOCK)
    bound = caller.binding(
        a.operator_repo, a.operator_ref, a.operator_tree, "synthetic", None
    )

    def tracker(phase="native", operation="host"):
        result = caller.FailureTracker(a.architecture, common.ARM_LOCK)
        result.trust_source(bound)
        for name in caller.DIAGNOSTIC_PHASES:
            result.enter_phase(name)
            if name == phase:
                break
        result.enter_operation(operation)
        caller._FAILURE_TRACKER = result
        return result

    track = tracker()
    process = caller.command(
        [sys.executable, "-B", "-c", "raise SystemExit(0)"], timeout=10
    )
    control(
        "diagnostic-positive-actual-process-zero",
        lambda: common.require(
            process.returncode == 0 and track.child == {"state": "executed", "exit": 0}
        ),
        0,
    )
    process = caller.command(
        [sys.executable, "-B", "-c", "raise SystemExit(7)"], timeout=10
    )
    original_error = caller.DiagnosticGuardError("private sentinel token=DO_NOT_EXPORT")
    record = track.failure_record(original_error)
    control(
        "diagnostic-actual-process-seven",
        lambda: common.require(
            record["terminal"]["kind"] == "process_exit"
            and record["terminal"]["child"] == {"state": "executed", "exit": 7}
        ),
        0,
    )
    failed_state = {"state": "executed", "exit": 2}
    projection = {"state": "valid", "record": record}
    control(
        "diagnostic-valid-projection",
        lambda: caller.validate_failure_projection(projection, expected, failed_state),
        0,
    )
    control(
        "diagnostic-success-null",
        lambda: caller.validate_failure_projection(
            {"state": "not_run", "record": None},
            expected,
            {"state": "executed", "exit": 0},
        ),
        0,
    )
    control(
        "diagnostic-missing-trust-unavailable",
        lambda: caller.validate_failure_projection(
            {"state": "unavailable", "record": None}, None, failed_state
        ),
        0,
    )
    for case, key, value in (
        ("wrong-source-ref", "operator_ref", "0" * 40),
        ("extra-private-text", "error", "token=DO_NOT_EXPORT"),
        ("boolean-caller-exit", "caller_exit", True),
        ("float-caller-exit", "caller_exit", 2.0),
    ):
        changed = dict(record, **{key: value})
        control(
            "diagnostic-" + case,
            lambda changed=changed: caller.validate_failure_record(changed, expected),
            2,
        )
    changed = json.loads(caller.canonical(record))
    changed["terminal"]["operation"] = "unknown-operation"
    control(
        "diagnostic-unknown-registry-operation",
        lambda: caller.validate_failure_record(changed, expected),
        2,
    )
    changed = json.loads(caller.canonical(record))
    changed["terminal"]["child"]["exit"] = 7.0
    control(
        "diagnostic-float-child-exit",
        lambda: caller.validate_failure_record(changed, expected),
        2,
    )
    control(
        "diagnostic-boolean-outer-exit",
        lambda: caller.validate_failure_projection(
            projection, expected, {"state": "executed", "exit": True}
        ),
        2,
    )
    track.enter_operation("daemon")
    control(
        "diagnostic-clears-stale-child",
        lambda: common.require(track.child == {"state": "not_run", "exit": None}),
        0,
    )
    control("diagnostic-phase-order", lambda: track.enter_phase("installed"), 2)
    track = tracker("controls", "missing_generated")
    process = caller.command(
        [sys.executable, "-B", "-c", "raise SystemExit(2)"], timeout=10
    )
    control(
        "diagnostic-expected-negative-control",
        lambda: caller.require_control_outcome(process, 2, True, "private"),
        0,
    )
    process = caller.command(
        [sys.executable, "-B", "-c", "raise SystemExit(0)"], timeout=10
    )
    control(
        "diagnostic-wrong-negative-control",
        lambda: caller.require_control_outcome(process, 2, True, "private"),
        2,
    )
    track = tracker()
    try:
        caller.command(
            [sys.executable, "-B", "-c", "import time;time.sleep(1)"], timeout=0.05
        )
    except subprocess.TimeoutExpired as error:
        timed = track.failure_record(error)
        control(
            "diagnostic-actual-timeout-null",
            lambda: common.require(
                timed["terminal"]["kind"] == "timeout"
                and timed["terminal"]["child"] == {"state": "unavailable", "exit": None}
            ),
            0,
        )
    else:
        raise ValueError("timeout control did not time out")
    track = tracker()
    caller.command([sys.executable, "-B", "-c", "raise SystemExit(7)"], timeout=10)
    private_case = a.scratch / "diagnostic-private"
    private_case.mkdir()
    control(
        "diagnostic-private-write",
        lambda: common.require(track.write_failure(private_case, original_error)),
        0,
    )
    diagnostic = private_case / "private/failure-diagnostic.json"
    control(
        "diagnostic-private-mode-privacy",
        lambda: common.require(
            diagnostic.stat().st_mode & 0o777 == 0o600
            and b"DO_NOT_EXPORT" not in diagnostic.read_bytes()
        ),
        0,
    )
    control(
        "diagnostic-private-reader",
        lambda: caller.validate_failure_projection(
            common.read_failure_projection(
                diagnostic, expected, failed_state, caller.validate_failure_projection
            ),
            expected,
            failed_state,
        ),
        0,
    )
    diagnostic.write_bytes(b'{"schema":1,"schema":2}')
    control(
        "diagnostic-malformed-unavailable",
        lambda: common.require(
            common.read_failure_projection(
                diagnostic, expected, failed_state, caller.validate_failure_projection
            )
            == {"state": "unavailable", "record": None}
        ),
        0,
    )
    caller._FAILURE_TRACKER = None
    result = {
        "schema": "aelira-latexml-ci-portable-controls-v1",
        "source_kind": "synthetic",
        "operator_ref": a.operator_ref,
        "operator_tree": a.operator_tree,
        "case_count": len(rows),
        "cases": rows,
    }
    (a.scratch / "controls.json").write_bytes(common.canonical(result))
    print(
        json.dumps(
            {
                "case_count": len(rows),
                "controls_sha256": common.regular(a.scratch / "controls.json")[
                    "sha256"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired):
        print("portable controls refused", file=sys.stderr)
        sys.exit(2)
