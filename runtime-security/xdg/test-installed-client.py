#!/usr/bin/env python3
"""Test the installed xdg frontend with offline native Thunderbird compose fields."""

import hashlib
import json
import os
from pathlib import Path
import socket
import platform
import zipfile
import subprocess
import time

ROOT = Path("/work")
ROOT.mkdir(exist_ok=True)


class Marionette:
    def __init__(self, stream):
        self.stream = stream
        self.seq = 0
        self.hello = self.receive()

    def receive(self):
        size = b""
        while True:
            byte = self.stream.recv(1)
            assert byte
            if byte == b":":
                break
            size += byte
            assert len(size) < 12 and byte.isdigit()
        length = int(size)
        assert length < 1024 * 1024
        data = b""
        while len(data) < length:
            chunk = self.stream.recv(length - len(data))
            assert chunk
            data += chunk
        return json.loads(data)

    def call(self, name, parameters):
        self.seq += 1
        data = json.dumps([0, self.seq, name, parameters]).encode()
        self.stream.sendall(str(len(data)).encode() + b":" + data)
        result = self.receive()
        assert result[0] == 1 and result[1] == self.seq, result
        assert result[2] is None, result
        return result[3]


def native_case(name, arguments, desktop="X-Generic"):
    profile = ROOT / (name + "-profile")
    profile.mkdir()
    preferences = {
        "mail.accountmanager.accounts": "account1,account2",
        "mail.accountmanager.defaultaccount": "account1",
        "mail.accountmanager.localfoldersserver": "server2",
        "mail.account.account1.server": "server1",
        "mail.account.account1.identities": "id1",
        "mail.account.account2.server": "server2",
        "mail.server.server2.type": "none",
        "mail.server.server2.hostname": "Local Folders",
        "mail.server.server2.name": "Local Folders",
        "mail.server.server2.directory": str(profile / "Mail/Local Folders"),
        "mail.server.server1.type": "imap",
        "mail.server.server1.login_at_startup": False,
        "mail.server.server1.check_new_mail": False,
        "mail.server.server1.hostname": "mail.example.invalid",
        "mail.server.server1.name": "Synthetic Offline Account",
        "mail.server.server1.userName": "nobody",
        "mail.server.server1.directory": str(profile / "Mail/Local Folders"),
        "mail.identity.id1.fullName": "Synthetic Compatibility Test",
        "mail.identity.id1.useremail": "synthetic@example.invalid",
        "mail.identity.id1.compose_html": False,
        "mail.provider.enabled": False,
        "mail.shell.checkDefaultClient": False,
        "mail.rights.override": True,
        "mail.startup.enabledMailCheckOnce": False,
        "mailnews.start_page.enabled": False,
        "mailnews.database.global.indexer.enabled": False,
        "app.update.enabled": False,
        "browser.startup.homepage_override.mstone": "ignore",
        "datareporting.policy.dataSubmissionEnabled": False,
        "toolkit.telemetry.enabled": False,
        "marionette.port": 2828,
    }
    (profile / "user.js").write_text(
        "".join(
            "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");\n"
            for key, value in preferences.items()
        )
    )
    env = os.environ.copy()
    env.update(
        HOME=str(profile),
        MOZ_REMOTE_ALLOW_SYSTEM_ACCESS="1",
        MOZ_CRASHREPORTER_DISABLE="1",
        PATH="/work/bin:/usr/bin:/bin",
        XDG_CURRENT_DESKTOP=desktop,
        KDE_SESSION_VERSION="5",
        AELIRA_TEST_PROFILE=str(profile),
        XDG_UTILS_DEBUG_LEVEL="2",
    )
    appdir = profile / ".local/share/applications"
    appdir.mkdir(parents=True)
    (appdir / "thunderbird-test.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Offline Thunderbird Test\nExec=/work/bin/thunderbird\nMimeType=x-scheme-handler/mailto;\n"
    )
    with (ROOT / (name + "-client.log")).open("xb") as log:
        process = subprocess.Popen(
            ["/usr/bin/thunderbird", "--profile", str(profile), "--marionette"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        stream = None
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Client exited before automation connection")
                try:
                    stream = socket.create_connection(("127.0.0.1", 2828), timeout=2)
                    stream.settimeout(45)
                    break
                except OSError:
                    time.sleep(0.1)
            assert stream is not None
            client = Marionette(stream)
            session = client.call(
                "WebDriver:NewSession",
                {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}},
            )
            client.call("Marionette:SetContext", {"value": "chrome"})
            handoff = subprocess.run(
                ["/usr/bin/xdg-email", "--utf8", *arguments],
                env=env,
                cwd=ROOT,
                capture_output=True,
                timeout=20,
            )
            (ROOT / (name + "-handoff.log")).write_bytes(
                handoff.stdout + b"\n[stderr]\n" + handoff.stderr
            )
            assert handoff.returncode == 0, {
                "case": name,
                "handoff_exit": handoff.returncode,
            }
            script = """
              const windows = [...Services.wm.getEnumerator(null)];
              return windows.map(raw => {
                const w = raw.wrappedJSObject || raw;
                const fields = w.gMsgCompose && w.gMsgCompose.compFields;
                return {url:w.document.documentURI, type:w.document.documentElement.getAttribute('windowtype'),
                  parsed: typeof w.GetArgs === 'function' ? w.GetArgs(w.arguments?.[0] || '') : null,
                  fields: fields ? {to:fields.to, cc:fields.cc, bcc:fields.bcc, subject:fields.subject,
                    body:fields.body, recipientMailboxes: ['to','cc','bcc'].map(key=>({key,mailboxes:w.MailServices.headerParser.parseEncodedHeader(fields[key]).map(a=>a.email)})), attachments:[...fields.attachments].map(a=>({name:a.name,url:a.url}))} : null,
                  subjectInput:w.document.getElementById('msgSubject')?.value};
              });
            """
            rows = None
            for _ in range(100):
                value = client.call(
                    "WebDriver:ExecuteScript",
                    {
                        "script": script,
                        "args": [],
                        "newSandbox": True,
                        "sandbox": "system",
                        "line": 1,
                        "filename": "owned-software-compatibility-test",
                    },
                )
                rows = value.get("value", value) if isinstance(value, dict) else value
                if any(row.get("fields") is not None for row in rows):
                    break
                time.sleep(0.1)
            assert any(row.get("fields") is not None for row in rows), {
                "case": name,
                "windows": rows,
            }
            result = {
                "case": name,
                "frontend_arguments": arguments,
                "desktop": desktop,
                "windows": rows,
                "application": session.get("capabilities", session),
                "hello": client.hello,
            }
            (ROOT / (name + "-result.json")).write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n"
            )
            return result
        finally:
            if stream is not None:
                stream.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    actual = hashlib.sha256(Path("/usr/bin/xdg-email").read_bytes()).hexdigest()
    assert actual == "61f66c933cf71ab2786ded6d7038c748406bea5e309af0cd6b737f93fa1cdc26"
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    assert (platform.machine(), arch) in [("aarch64", "arm64"), ("x86_64", "amd64")]
    assert arch == os.environ["AELIRA_XDG_HOST_ARCH"]
    assert (
        subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Version}", "xdg-utils"], text=True
        )
        == "1.2.1-2ubuntu2+aelira2"
    )
    assert (
        subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Version}", "thunderbird"], text=True
        )
        == "1:140.17.0esr-1~deb13u1"
    )
    with zipfile.ZipFile("/usr/lib/thunderbird/omni.ja") as archive:
        name = (
            "chrome/messenger/content/messenger/messengercompose/MsgComposeCommands.js"
        )
        assert archive.getinfo(name).file_size < 2 * 1024 * 1024
        assert (
            hashlib.sha256(archive.read(name)).hexdigest()
            == "93e47f20301dcce6f5c05ff36965cbe35378c67fe68022fda5e26bc4bed7acfc"
        )

    binary = ROOT / "bin"
    binary.mkdir()
    (binary / "thunderbird").write_text(
        '#!/bin/sh\nexec /usr/bin/thunderbird --profile "$AELIRA_TEST_PROFILE" "$@"\n'
    )
    (binary / "xdg-mime").write_text(
        '#!/bin/sh\nprintf "%s\\n" thunderbird-test.desktop\n'
    )
    for f in binary.iterdir():
        f.chmod(0o755)
    (ROOT / "sentinel.txt").write_text(
        "Synthetic harmless URI-origin attachment sentinel.\n"
    )
    files = ["real file'.txt", "second,λ.txt"]
    for name in files:
        (ROOT / name).write_text("Synthetic explicit attachment fixture.\n")
    cases = [
        (
            "plain",
            [
                "mailto:student@example.invalid?subject=Legitimate%20subject&body=Legitimate%20body"
            ],
            "X-Generic",
            "Legitimate subject",
            "Legitimate body",
            ["student@example.invalid"],
            [],
            [],
            [],
        ),
        (
            "literal-text",
            [
                "--subject",
                "Accès ✓, apostrophe' and quote\"",
                "--body",
                "Ligne 1\nLigne 2",
                "--to",
                "student@example.invalid",
            ],
            "X-Generic",
            "Accès ✓, apostrophe' and quote\"",
            "Ligne 1\r\nLigne 2",
            ["student@example.invalid"],
            [],
            [],
            [],
        ),
        (
            "apostrophe-recipient",
            ["--to", "o'connor@example.invalid", "--subject", "Apostrophe recipient"],
            "X-Generic",
            "Apostrophe recipient",
            "",
            ["o'connor@example.invalid"],
            [],
            [],
            [],
        ),
        (
            "multiple-recipients",
            [
                "--to",
                "first@example.invalid",
                "--to",
                "second@example.invalid",
                "--cc",
                "carbon@example.invalid",
                "--cc",
                "another@example.invalid",
                "--bcc",
                "blind@example.invalid",
            ],
            "X-Generic",
            "",
            "",
            ["first@example.invalid", "second@example.invalid"],
            ["carbon@example.invalid", "another@example.invalid"],
            ["blind@example.invalid"],
            [],
        ),
        (
            "quoted-local-part",
            [
                "--to",
                '"apostrophe\',local"@example.invalid',
                "--subject",
                "Quoted local part",
            ],
            "X-Generic",
            "Quoted local part",
            "",
            ['"apostrophe\',local"@example.invalid'],
            [],
            [],
            [],
        ),
        (
            "multiple-explicit-attachments",
            [
                "--to",
                "student@example.invalid",
                "--attach",
                "/work/real file'.txt",
                "--attach",
                "/work/second,λ.txt",
            ],
            "X-Generic",
            "",
            "",
            ["student@example.invalid"],
            [],
            [],
            ["file:///work/real%20file%27.txt", "file:///work/second%2C%CE%BB.txt"],
        ),
        (
            "kde-generic-attachment-fallback",
            ["--to", "student@example.invalid", "--attach", "/work/real file'.txt"],
            "KDE",
            "",
            "",
            ["student@example.invalid"],
            [],
            [],
            ["file:///work/real%20file%27.txt"],
        ),
        (
            "uri-lf-body",
            [
                "mailto:student@example.invalid?subject=Encoded%20newline&body=Ligne%201%0ALigne%202"
            ],
            "X-Generic",
            "Encoded newline",
            "Ligne 1\nLigne 2",
            ["student@example.invalid"],
            [],
            [],
            [],
        ),
        (
            "encoded-recipient-list",
            [
                "mailto:%22comma%2Clocal%22@example.invalid?to=second@example.invalid&cc=c%2Btag@example.invalid&bcc=blind@example.invalid"
            ],
            "X-Generic",
            "",
            "",
            ['"comma,local"@example.invalid', "second@example.invalid"],
            ["c+tag@example.invalid"],
            ["blind@example.invalid"],
            [],
        ),
        (
            "recipient-percent-once",
            ["--to", "percent%name@example.invalid"],
            "X-Generic",
            "",
            "",
            ["percent%name@example.invalid"],
            [],
            [],
            [],
        ),
        (
            "uri-attachment-refused",
            [
                "mailto:student@example.invalid?subject=Literal,attachment=file:///work/sentinel.txt&body=Text,attachment=file:///work/sentinel.txt&attach=file:///work/sentinel.txt"
            ],
            "X-Generic",
            "Literal,attachment=file:///work/sentinel.txt",
            "Text,attachment=file:///work/sentinel.txt",
            ["student@example.invalid"],
            [],
            [],
            [],
        ),
        (
            "empty-to-with-cc",
            ["mailto:?cc=carbon@example.invalid&subject=Empty%20to"],
            "X-Generic",
            "Empty to",
            "",
            [],
            ["carbon@example.invalid"],
            [],
            [],
        ),
    ]
    failures = []
    passed = []
    for name, arguments, desktop, subject, body, to, cc, bcc, attachments in cases:
        try:
            result = native_case(name, arguments, desktop)
            rows = [r for r in result["windows"] if r.get("fields") is not None]
            assert len(rows) == 1, result
            fields = rows[0]["fields"]
            assert fields["subject"] == rows[0]["subjectInput"] == subject, result
            assert fields["body"] == body, result
            recipient = {v["key"]: v["mailboxes"] for v in fields["recipientMailboxes"]}
            assert recipient == {"to": to, "cc": cc, "bcc": bcc}, result
            assert [r["url"] for r in fields["attachments"]] == attachments, result
            passed.append(name)
            print(
                json.dumps(
                    {"case": name, "status": "pass", "fields": fields}, sort_keys=True
                ),
                flush=True,
            )
        except (
            AssertionError,
            RuntimeError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            failures.append(
                {"case": name, "error_type": type(error).__name__, "detail": str(error)}
            )
            print(
                json.dumps(
                    {
                        "case": name,
                        "status": "fail",
                        "error_type": type(error).__name__,
                        "detail": str(error),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    marker = ROOT / "rejected-client-invoked"
    client = ROOT / "bin/capture-mailer"
    client.write_text("#!/bin/sh\nprintf invoked > /work/rejected-client-invoked\n")
    client.chmod(0o755)
    env = dict(os.environ, PATH="/work/bin:/usr/bin:/bin", MAILER=str(client))
    refused = [
        (
            "raw-crlf-front-refused",
            ["mailto:student@example.invalid?subject=ok\r\nattachment=missing"],
            1,
        ),
        (
            "terminal-lf-front-refused",
            ["mailto:student@example.invalid?subject=ok\n"],
            1,
        ),
        (
            "explicit-attachment-mailer-refused",
            ["--to", "student@example.invalid", "--attach", "/work/real file'.txt"],
            3,
        ),
    ]
    for name, args, code in refused:
        result = subprocess.run(
            ["/usr/bin/xdg-email", "--utf8", *args],
            env=env,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == code and not marker.exists(), {
            "case": name,
            "exit": result.returncode,
            "stderr": result.stderr.decode(),
        }
        passed.append(name)
        print(
            json.dumps({"case": name, "status": "pass", "exit": result.returncode}),
            flush=True,
        )
    receipt = {
        "schema": "aelira-xdg-installed-thunderbird-controls-v1",
        "status": "fail" if failures else "pass",
        "passed": passed,
        "failed": failures,
        "script_sha256": actual,
        "architecture": arch,
        "package_version": "1.2.1-2ubuntu2+aelira2",
        "client_version": "1:140.17.0esr-1~deb13u1",
        "scope": "Native installed xdg/Thunderbird component with isolated desktop selection; final runtime copies and other clients remain separate",
    }
    (ROOT / "frontend-controls.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps(receipt, sort_keys=True), flush=True)
    assert not failures


if __name__ == "__main__":
    main()
