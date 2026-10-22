#!/usr/bin/env python3
"""Portable local controls; synthetic filesystem/Git checks are not producer proof."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import run as common

# The started-container control waits for readiness instead of guessing a start-up time.
READY_DEADLINE = 30.0
READY_POLL = 0.1
READY_TIMEOUT = 2
READY_OUTPUT = b"ready"
READY_REASONS = frozenset(
    (
        "cidfile-not-written",
        "ready-not-observed",
        "exited-before-ready",
        "unexpected-output",
        "no-timeout",
    )
)
PHASE = "startup"


class ReadinessFailure(ValueError):
    """A started-container control failed for one fixed, non-sensitive reason."""

    def __init__(self, reason):
        super().__init__("started-container control refused")
        self.reason = reason if reason in READY_REASONS else "unknown"


class ControlRefused(ValueError):
    """A control returned an exit different from the one it expects."""

    def __init__(self, name, expected, code, cause):
        super().__init__("control result differs")
        self.name, self.expected, self.code, self.cause = name, expected, code, cause


def describe(error):
    """Fixed-form description of an exception chain; classes and exit codes only.

    Exception text, paths and environment are never printed: the message of an
    OSError, TimeoutExpired or plain ValueError can carry any of them.
    """
    parts = []
    for _ in range(4):
        if error is None:
            break
        if isinstance(error, common.ProcessFailure):
            code = error.code if type(error.code) is int else "?"
            parts.append("ProcessFailure(exit=" + str(code) + ")")
        elif isinstance(error, ReadinessFailure):
            parts.append("ReadinessFailure(" + error.reason + ")")
        else:
            parts.append(type(error).__name__)
        error = error.__cause__
    return " <- ".join(parts) if parts else "no error raised"


def refusal_message(error, phase):
    if isinstance(error, ControlRefused):
        return (
            "portable controls refused: control "
            + error.name
            + ": expected exit "
            + str(error.expected)
            + ", got "
            + str(error.code)
            + ": "
            + describe(error.cause)
        )
    return "portable controls refused: " + phase + ": " + describe(error)


def run_control(name, fn, expected, rows):
    global PHASE
    PHASE = "control " + name
    cause = None
    try:
        fn()
        code = 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        code, cause = 2, error
    rows.append({"case": name, "exit": code, "expected_exit": expected})
    if code != expected:
        raise ControlRefused(name, expected, code, cause)
    PHASE = "between controls"


class StartedProcess:
    """Non-blocking stdout view of a started child process."""

    def __init__(self, argv):
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.fd = self.process.stdout.fileno()
        os.set_blocking(self.fd, False)
        self.output, self.eof = b"", False

    def poll_output(self):
        while not self.eof and len(self.output) <= 4096:
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                return
            if not chunk:
                self.eof = True
            self.output += chunk

    def wait(self, timeout):
        return self.process.wait(timeout)

    def kill(self):
        self.process.kill()
        self.process.wait()
        self.process.stdout.close()


def cid_written(cidfile):
    try:
        return re.fullmatch(r"[0-9a-f]{64}\n?", cidfile.read_text()) is not None
    except (OSError, UnicodeError):
        return False


def started_container_timeout(
    start,
    cidfile,
    cleanup,
    *,
    deadline=READY_DEADLINE,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """Start a container, wait for readiness, then test timeout and cleanup.

    The timeout under test begins only once the container has printed ready and
    its identity file exists, so it does not depend on start-up latency. Start-up
    gets one bounded deadline and no retry.
    """
    handle, failure = None, None
    try:
        handle = start()
        limit = clock() + deadline
        while True:
            handle.poll_output()
            written = cid_written(cidfile)
            if not READY_OUTPUT.startswith(handle.output):
                raise ReadinessFailure("unexpected-output")
            if written and handle.output == READY_OUTPUT:
                break
            if handle.eof:
                raise ReadinessFailure("exited-before-ready")
            if clock() >= limit:
                raise ReadinessFailure(
                    "ready-not-observed" if written else "cidfile-not-written"
                )
            sleep(READY_POLL)
        try:
            handle.wait(READY_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
        else:
            raise ReadinessFailure("no-timeout")
    except BaseException as error:
        failure = error
        raise
    finally:
        if handle is not None:
            handle.kill()
        if failure is None:
            cleanup(cidfile)
        elif cid_written(cidfile):
            try:
                cleanup(cidfile)
            except (ValueError, OSError, subprocess.TimeoutExpired):
                pass  # keep the original failure; it is the one worth reporting


def native_container_argv(cidfile):
    return [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--cidfile",
        str(cidfile),
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
    ]


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
        run_control(name, fn, expected, rows)

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

        # Refusal classes (#737). Each mapping first drives the same probe to its
        # success path, so a refusal is read only from a probe proven to pass.
        def refusal_class(answers):
            SequenceConnection.answers = answers
            SequenceConnection.calls = SequenceConnection.closes = 0
            try:
                data = common.snapshot_file("dists/resolute/InRelease", 100)
            except ValueError as error:
                return common.refusal_class_of(error)
            common.require(data == b"known bounded metadata", "probe success differs")
            return None

        def class_case(name, answers, expected):
            control(
                "refusal-class-" + name + "-positive-first",
                lambda: common.require(refusal_class([good]) is None),
                0,
            )
            control(
                "refusal-class-" + name,
                lambda: common.require(refusal_class(answers) == expected),
                0,
            )

        for name, answers, expected in (
            ("http-401-auth", [Response(401, b"")], "upstream_http_auth"),
            ("http-403-auth", [Response(403, b"")], "upstream_http_auth"),
            ("http-5xx-exhausted", [Response(503, b"")] * 5, "upstream_http_5xx"),
            (
                "http-500-mixed-exhausted",
                [Response(s, b"") for s in (500, 502, 503, 504, 500)],
                "upstream_http_5xx",
            ),
            ("http-501-not-retried", [Response(501, b"")], "upstream_http_5xx"),
            ("http-404-other-4xx", [Response(404, b"")], "upstream_http_4xx"),
            ("http-400-other-4xx", [Response(400, b"")], "upstream_http_4xx"),
            ("http-302-other", [Response(302, b"")], "upstream_http_other"),
            ("transport-exhausted", [ConnectionResetError()] * 5, "transport"),
            (
                "http-exception-exhausted",
                [common.http.client.HTTPException()] * 5,
                "transport",
            ),
            ("timeout-exhausted", [TimeoutError()] * 5, "timeout"),
            (
                "transient-then-timeout",
                [Response(503, b"")] * 4 + [TimeoutError()],
                "timeout",
            ),
            ("tls", [common.ssl.SSLError()], "tls"),
            ("certificate", [common.ssl.SSLCertVerificationError()], "tls"),
            ("empty-body-integrity", [Response(200, b"")], "integrity"),
            ("oversized-body-integrity", [Response(200, b"x" * 101)], "integrity"),
        ):
            class_case(name, answers, expected)
    finally:
        common.http.client.HTTPSConnection = original_connection
        common.time.sleep = original_sleep
    control(
        "snapshot-empty-CA-archive-refused", lambda: common.snapshot_ca_bundle(b""), 2
    )

    # Labelling keeps the original exception type and exit, so stage receipts
    # and retry eligibility are unchanged; an existing label is never replaced.
    def labelled(name, raise_error, expected_class, expected_type, expected_code):
        def check():
            try:
                with common.refusal(name):
                    raise_error()
            except (ValueError, OSError) as error:
                common.require(
                    type(error) is expected_type
                    and common.refusal_class_of(error) == expected_class
                    and getattr(error, "code", None) == expected_code
                )
                return
            raise AssertionError("label probe raised nothing")

        control(
            "refusal-label-" + expected_class + "-" + expected_type.__name__, check, 0
        )

    def unlabelled_success():
        with common.refusal("integrity"):
            value = "passed"
        common.require(value == "passed")

    control("refusal-label-success-passes-through", unlabelled_success, 0)

    def gpgv_failure():
        raise common.ProcessFailure(1)

    def bound_failure():
        common.require(False, "snapshot resource bound")

    def already_auth():
        raise common.classified(
            ValueError("snapshot request refused"), "upstream_http_auth"
        )

    labelled("integrity", gpgv_failure, "integrity", common.ProcessFailure, 1)
    labelled("integrity", bound_failure, "integrity", ValueError, None)
    labelled("integrity", already_auth, "upstream_http_auth", ValueError, None)
    control(
        "refusal-class-unlabelled-is-unknown",
        lambda: common.require(
            common.refusal_class_of(ValueError("x")) == "unknown"
            and common.refusal_class_of(common.ProcessFailure(100)) == "unknown"
            and common.refusal_class_of(subprocess.TimeoutExpired("capture", 1200))
            == "timeout"
        ),
        0,
    )
    control(
        "refusal-class-undeclared-label-refused",
        lambda: common.classified(ValueError("x"), "upstream_http_418"),
        2,
    )
    worker = {
        "file": "perl_1_all.deb",
        "size": 512,
        "sha256": "a" * 64,
        "apt": {"state": "executed", "exit": 0},
        "result": {"state": "executed", "exit": 0},
    }
    control(
        "refusal-class-process-success-is-unknown-positive-first",
        lambda: common.require(
            common.capture_process_refusal_class({"downloads": [worker]}) == "unknown"
        ),
        0,
    )
    control(
        "refusal-class-worker-check-after-APT-success-is-integrity",
        lambda: common.require(
            common.capture_process_refusal_class(
                {"downloads": [worker | {"result": {"state": "executed", "exit": 1}}]}
            )
            == "integrity"
        ),
        0,
    )
    control(
        "refusal-class-APT-download-refusal-stays-unknown",
        lambda: common.require(
            common.capture_process_refusal_class(
                {
                    "downloads": [
                        worker
                        | {
                            "apt": {"state": "executed", "exit": 100},
                            "result": {"state": "executed", "exit": 100},
                        }
                    ]
                }
            )
            == "unknown"
            and common.capture_process_refusal_class({"downloads": []}) == "unknown"
        ),
        0,
    )
    control(
        "refusal-class-vocabulary-matches-coordinator-and-summary",
        lambda: common.require(
            tuple(
                re.findall(
                    r"'([a-z0-9_]+)'",
                    re.search(
                        r"refusalClasses = \[(.*?)\] as const",
                        Path(common.__file__).with_name("capture-retry.ts").read_text(),
                        re.S,
                    ).group(1),
                )
            )
            == common.REFUSAL_CLASSES
            == tuple(
                re.findall(
                    r'"([a-z0-9_]+)"',
                    re.search(
                        r"\[(\"upstream_http_auth\".*?)\] as \$classes",
                        (
                            Path(common.__file__).parents[2]
                            / ".github/workflows/ci.yml"
                        ).read_text(),
                        re.S,
                    ).group(1),
                )
            )
        ),
        0,
    )
    control("source-selector-without-capture-state", lambda: common.selector(a), 0)
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
    # Readiness, timeout and diagnostic controls; none needs Docker.
    ready_cid = a.scratch / "ready.cid"

    class Clock:
        now = 0.0

        @classmethod
        def tick(cls, seconds):
            cls.now += seconds
            for at, action in list(Clock.events):
                if cls.now >= at:
                    Clock.events.remove((at, action))
                    action()

        events = []

    class Fake:
        """Container whose cid file and output appear on a simulated clock."""

        def __init__(self, cid_at, ready_at, exits_at=None, output=b"ready"):
            self.output, self.eof, self.killed, self.exits_at = b"", False, 0, exits_at
            Clock.events[:] = [
                (cid_at, lambda: ready_cid.write_text("c" * 64)),
                (ready_at, lambda: setattr(self, "pending", output)),
            ]
            self.pending = b""
            ready_cid.unlink(missing_ok=True)

        def poll_output(self):
            self.output = self.pending
            if self.exits_at is not None and Clock.now >= self.exits_at:
                self.eof = True

        def wait(self, timeout):
            if self.exits_at is not None and Clock.now >= self.exits_at:
                return 0
            raise subprocess.TimeoutExpired("fake", timeout)

        def kill(self):
            self.killed += 1

    def readiness(fake, cleaned, deadline=READY_DEADLINE, cleanup=None):
        Clock.now = 0.0
        cleaned.clear()
        started = []

        def start():
            started.append(fake)
            return fake

        started_container_timeout(
            start,
            ready_cid,
            cleanup or cleaned.append,
            deadline=deadline,
            clock=lambda: Clock.now,
            sleep=Clock.tick,
        )
        common.require(fake.killed == 1, "client not stopped exactly once")

    def reason(fake, expected, cleaned, deadline=READY_DEADLINE):
        def attempt():
            try:
                readiness(fake, cleaned, deadline)
            except ReadinessFailure as error:
                common.require(error.reason == expected, "wrong readiness reason")
                common.require(fake.killed == 1, "client not stopped exactly once")
                raise

        return attempt

    cleaned = []
    control(
        "started-container-immediate-ready-positive",
        lambda: readiness(Fake(0, 0), cleaned),
        0,
    )
    control(
        "started-container-immediate-ready-cleaned-once",
        lambda: common.require(cleaned == [ready_cid]),
        0,
    )
    # A 12 second start-up is what the former fixed two second timeout refused.
    control(
        "started-container-slow-start-positive",
        lambda: readiness(Fake(9, 12), cleaned),
        0,
    )
    control(
        "started-container-slow-start-cleaned-once",
        lambda: common.require(cleaned == [ready_cid]),
        0,
    )
    control(
        "started-container-slow-start-fixed-two-second-deadline-refused",
        reason(Fake(9, 12), "cidfile-not-written", cleaned, deadline=2),
        2,
    )
    control(
        "started-container-slow-ready-after-cid-positive",
        lambda: readiness(Fake(1, 25), cleaned),
        0,
    )
    control(
        "started-container-deadline-without-ready-refused",
        reason(Fake(1, 31), "ready-not-observed", cleaned),
        2,
    )
    control(
        "started-container-deadline-without-cid-refused",
        reason(Fake(31, 1), "cidfile-not-written", cleaned),
        2,
    )
    control(
        "started-container-exit-before-ready-refused",
        reason(Fake(0, 1000, exits_at=3), "exited-before-ready", cleaned),
        2,
    )
    control(
        "started-container-unexpected-output-refused",
        reason(Fake(0, 1, output=b"other"), "unexpected-output", cleaned),
        2,
    )
    control(
        "started-container-exit-instead-of-timeout-refused",
        reason(Fake(0, 0, exits_at=0.05), "no-timeout", cleaned),
        2,
    )
    control(
        "started-container-failure-without-cid-refused",
        reason(Fake(31, 1), "cidfile-not-written", cleaned),
        2,
    )
    control(
        "started-container-failure-without-cid-no-cleanup-attempted",
        lambda: common.require(cleaned == []),
        0,
    )

    attempts = []

    def cleanup_unavailable(_):
        attempts.append(1)
        raise common.CaptureUnavailable("cleanup")

    def cleanup_failure_kept():
        attempts.clear()
        try:
            readiness(Fake(1, 31), cleaned, cleanup=cleanup_unavailable)
        except ReadinessFailure as error:
            common.require(
                error.reason == "ready-not-observed" and attempts == [1],
                "original failure lost or cleanup skipped",
            )
        else:
            raise ValueError("readiness failure expected")

    control("started-container-cleanup-failure-keeps-original", cleanup_failure_kept, 0)
    control(
        "started-container-cleanup-failure-after-success-surfaces",
        lambda: readiness(Fake(0, 0), cleaned, cleanup=cleanup_unavailable),
        2,
    )
    ready_cid.unlink(missing_ok=True)

    # A real child process whose cid file and output are delayed one second.
    real_script = 'sleep 1; printf "%064d" 0 > "$1"; printf ready; exec sleep 30'
    real_cleaned = []

    def real_child(deadline):
        started_container_timeout(
            lambda: StartedProcess(
                ["/bin/sh", "-c", real_script, "sh", str(ready_cid)]
            ),
            ready_cid,
            real_cleaned.append,
            deadline=deadline,
        )

    control("started-real-child-slow-start-positive", lambda: real_child(30), 0)
    control(
        "started-real-child-slow-start-cleaned-once",
        lambda: common.require(real_cleaned == [ready_cid]),
        0,
    )
    ready_cid.unlink(missing_ok=True)
    real_cleaned.clear()
    control(
        "started-real-child-short-deadline-refused",
        lambda: real_child(0.2),
        2,
    )
    control(
        "started-real-child-short-deadline-no-cid-no-cleanup",
        lambda: common.require(real_cleaned == []),
        0,
    )
    ready_cid.unlink(missing_ok=True)

    # Diagnostics: class and fixed fields only, never exception text.
    leak = "/home/runner/secret-path TOKEN=hunter2"
    chained = common.CaptureUnavailable("cleanup")
    chained.__cause__ = common.ProcessFailure(125)
    for case, error, expected in (
        (
            "process-failure",
            common.ProcessFailure(125),
            "ProcessFailure(exit=125)",
        ),
        (
            "timeout",
            subprocess.TimeoutExpired(["docker", leak], 2, output=leak.encode()),
            "TimeoutExpired",
        ),
        ("os-error", FileNotFoundError(2, leak, leak), "FileNotFoundError"),
        ("plain-os-error", OSError(leak), "OSError"),
        ("value-error", ValueError(leak), "ValueError"),
        (
            "readiness",
            ReadinessFailure("ready-not-observed"),
            "ReadinessFailure(ready-not-observed)",
        ),
        (
            "readiness-unlisted-reason",
            ReadinessFailure(leak),
            "ReadinessFailure(unknown)",
        ),
        ("chained-cleanup", chained, "CaptureUnavailable <- ProcessFailure(exit=125)"),
    ):
        text = refusal_message(error, "native-base-pull")
        control(
            "diagnostic-" + case,
            lambda text=text, expected=expected: common.require(
                text == "portable controls refused: native-base-pull: " + expected
                and "secret" not in text
                and "hunter2" not in text
                and "\n" not in text
            ),
            0,
        )
    control(
        "diagnostic-names-failed-control",
        lambda: common.require(
            refusal_message(
                ControlRefused("some-control", 0, 2, ReadinessFailure("no-timeout")),
                "x",
            )
            == "portable controls refused: control some-control: expected exit 0, got 2: "
            "ReadinessFailure(no-timeout)"
            and refusal_message(ControlRefused("other", 2, 0, None), "x")
            == "portable controls refused: control other: expected exit 2, got 0: no error raised"
        ),
        0,
    )
    mismatch = []
    control(
        "control-mismatch-raises-named-refusal",
        lambda: run_control("synthetic", lambda: None, 2, mismatch),
        2,
    )
    control(
        "control-mismatch-still-recorded",
        lambda: common.require(
            mismatch == [{"case": "synthetic", "exit": 0, "expected_exit": 2}]
        ),
        0,
    )
    # The top-level handler prints the phase and class for an actual refusal.
    existing = a.scratch / "existing-scratch"
    existing.mkdir()
    handler = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            "--scratch",
            str(existing),
            "--operator-repo",
            str(existing),
            "--operator-ref",
            "0" * 40,
            "--operator-tree",
            "0" * 40,
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    control(
        "top-level-refusal-names-phase-and-class",
        lambda: common.require(
            handler.returncode == 2
            and handler.stdout == b""
            and handler.stderr == b"portable controls refused: startup: ValueError\n"
        ),
        0,
    )
    if a.native_cleanup:
        global PHASE
        PHASE = "native-requirements"
        common.require(
            sys.platform.startswith("linux") and os.getuid() != 0,
            "native nonroot cleanup control required",
        )
        actual_cid = a.scratch / "actual-container.cid"
        # Fetch only the immutable base so start-up below never includes a pull.
        PHASE = "native-base-pull"
        common.command(["/usr/bin/docker", "pull", common.BASE], timeout=600)
        control(
            "capture-cleanup-actual-started-timeout",
            lambda: started_container_timeout(
                lambda: StartedProcess(native_container_argv(actual_cid)),
                actual_cid,
                common.cleanup_capture,
            ),
            0,
        )
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
    plan_dir = a.scratch / "download-plan-controls"
    plan_dir.mkdir()
    plan = plan_dir / "plan.txt"
    awk = plan_dir / "plan.awk"
    awk.write_text(common.DOWNLOAD_PLAN_AWK)
    uri = "https://snapshot.ubuntu.com/ubuntu/20261013T060000Z/pool/main/p/perl/perl_1_all.deb"
    positive = "'" + uri + "' perl_1_all.deb 512 SHA256:" + "a" * 64 + "\n"

    def check_plan(text, expected=0):
        plan.write_text(text)
        result = subprocess.run(
            ["/usr/bin/awk", "-f", str(awk), str(plan)],
            capture_output=True,
            timeout=5,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        common.require(
            result.returncode == expected, "plan parser original exit differs"
        )
        if expected == 0:
            values = common.download_plan(plan)
            common.require(
                result.stdout
                == b"".join(
                    str(value).encode("ascii") + b"\0"
                    for row in values
                    for value in (
                        row["uri"],
                        row["file"],
                        row["size"],
                        "SHA256:" + row["sha256"],
                    )
                ),
                "plan arguments differ",
            )
        else:
            common.require(result.stdout == b"", "invalid plan emitted arguments")
            try:
                common.download_plan(plan)
            except ValueError:
                return
            raise ValueError("invalid plan accepted")

    control(
        "download-plan-positive-complete-argument-parity",
        lambda: check_plan(positive),
        0,
    )
    for name, text in (
        ("wrong-host", positive.replace("snapshot.ubuntu.com", "example.com")),
        ("http", positive.replace("https:", "http:")),
        ("wrong-snapshot", positive.replace("20261013T060000Z", "20261014T060000Z")),
        ("dot-traversal", positive.replace("/p/perl/", "/p/../")),
        ("encoded-slash", positive.replace("perl_1_all.deb'", "perl%2f1_all.deb'")),
        ("shell-value", positive.replace("perl_1_all.deb 512", "$(id).deb 512")),
        ("nested-file", positive.replace("' perl_1_all.deb", "' ../perl.deb")),
        ("duplicate-name", positive + positive),
        ("weak-hash", positive.replace("SHA256:", "MD5Sum:")),
        ("short-hash", positive[:-2] + "\n"),
        ("zero-size", positive.replace(" 512 ", " 0 ")),
        ("negative-size", positive.replace(" 512 ", " -1 ")),
        ("file-bound", positive.replace(" 512 ", " 200000001 ")),
        ("extra-field", positive.rstrip() + " extra\n"),
        ("empty", "Reading package lists...\n"),
        (
            "total-bound",
            "".join(
                positive.replace(
                    "' perl_1_all.deb", "' perl_" + str(i) + "_all.deb"
                ).replace(" 512 ", " 200000000 ")
                for i in range(6)
            ),
        ),
        (
            "count-bound",
            "".join(
                positive.replace("' perl_1_all.deb", "' perl_" + str(i) + "_all.deb")
                for i in range(1101)
            ),
        ),
    ):
        control(
            "download-plan-" + name + "-refused-without-arguments",
            lambda text=text: check_plan(text, 2),
            0,
        )
    status_dir = a.scratch / "capture-status-controls"
    status_dir.mkdir()
    control(
        "capture-status-unstarted",
        lambda: common.require(
            common.capture_status(status_dir, "download")
            == {"state": "not_run", "exit": None}
        ),
        0,
    )
    (status_dir / "download.started").write_bytes(b"1\n")
    control(
        "capture-status-started-timeout",
        lambda: common.require(
            common.capture_status(status_dir, "download")
            == {"state": "unavailable", "exit": None}
        ),
        0,
    )
    (status_dir / "download.exit").write_bytes(b"123\n")
    control(
        "capture-status-original-parallel-exit",
        lambda: common.require(
            common.capture_status(status_dir, "download")
            == {"state": "executed", "exit": 123}
        ),
        0,
    )
    (status_dir / "download.exit").write_bytes(b"1000\n")
    control(
        "capture-status-out-of-range",
        lambda: common.capture_status(status_dir, "download"),
        2,
    )
    (status_dir / "download.started").write_bytes(b"0\n")
    control(
        "capture-status-marker-substitution",
        lambda: common.capture_status(status_dir, "download"),
        2,
    )
    interrupted = a.scratch / "interrupted-capture-controls"
    (interrupted / "status").mkdir(parents=True)
    for name in ("setup", "update", "download"):
        (interrupted / "status" / (name + ".started")).write_bytes(b"1\n")
    for name in ("setup", "update"):
        (interrupted / "status" / (name + ".exit")).write_bytes(b"0\n")
    process = {"downloads": [], "download_plan_sha256": None}
    common.observe_capture_process(interrupted, process)
    control(
        "capture-observe-completed-before-timeout",
        lambda: common.require(
            process["stages"]["setup"]
            == process["stages"]["update"]
            == {"state": "executed", "exit": 0}
            and process["stages"]["download"] == {"state": "unavailable", "exit": None}
            and process["stages"]["select"]
            == process["stages"]["restore"]
            == {"state": "not_run", "exit": None}
        ),
        0,
    )
    (interrupted / "status/plan.started").write_bytes(b"1\n")
    (interrupted / "status/plan.exit").write_bytes(b"0\n")
    (interrupted / "download-plan.txt").write_text(positive)
    (interrupted / "download-args").write_bytes(
        b"\0".join((uri.encode(), b"perl_1_all.deb", b"512", b"SHA256:" + b"a" * 64))
        + b"\0"
    )
    workers = interrupted / "status/workers"
    workers.mkdir()
    (workers / "perl_1_all.deb.started").write_bytes(b"1\n")
    (workers / "perl_1_all.deb.exit").write_bytes(b"2\n")

    def observed_worker():
        receipt = {"downloads": [], "download_plan_sha256": None}
        common.observe_capture_process(interrupted, receipt)
        return receipt["downloads"][0]

    control(
        "capture-worker-prelaunch-refusal-is-not-APT",
        lambda: common.require(
            observed_worker()["apt"] == {"state": "not_run", "exit": None}
            and observed_worker()["result"] == {"state": "executed", "exit": 2}
        ),
        0,
    )
    (workers / "perl_1_all.deb.apt.started").write_bytes(b"1\n")
    control(
        "capture-worker-started-APT-without-terminal-unavailable",
        lambda: common.require(
            observed_worker()["apt"] == {"state": "unavailable", "exit": None}
        ),
        0,
    )
    (workers / "perl_1_all.deb.apt.exit").write_bytes(b"100\n")
    (workers / "perl_1_all.deb.exit").write_bytes(b"100\n")
    control(
        "capture-worker-original-APT-refusal-retained",
        lambda: common.require(
            observed_worker()["apt"]
            == observed_worker()["result"]
            == {"state": "executed", "exit": 100}
        ),
        0,
    )
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
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(refusal_message(error, PHASE), file=sys.stderr)
        sys.exit(2)
