"""Confine LaTeX tools to their own conversion directory.

TeX, LaTeXML and pandoc resolve file names written in the document, including
absolute paths and forms that no source pattern check can recognise. Each tool
therefore runs with a minimal environment and under a Linux Landlock ruleset:
it may write only inside its conversion directory, read the declared source
directory, and read and execute the installed system files it needs. Other
application files, uploads, mounted keys and /proc stay unreadable.

This file is also the launcher. It applies the ruleset to itself, then
executes the tool, which inherits the restriction. It deliberately imports
nothing from the application so it can run with ``python -I``.
"""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys
from pathlib import Path

LANDLOCK_CREATE_RULESET = 444
LANDLOCK_ADD_RULE = 445
LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
REFER = 1 << 13
TRUNCATE = 1 << 14
IOCTL_DEV = 1 << 15

READ_ONLY = EXECUTE | READ_FILE | READ_DIR
SYSTEM_READ_ONLY = (
    "/usr",
    "/etc",
    "/bin",
    "/sbin",
    "/lib",
    "/var/lib/texmf",
    "/var/cache/fontconfig",
)
DEVICES = ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random")
UNAVAILABLE = 126


def confined(
    args: list[str], directory: Path, read_only: tuple[Path, ...] = ()
) -> list[str]:
    """Return a command that runs ``args`` confined to ``directory``."""
    command = [sys.executable, "-I", str(Path(__file__).resolve())]
    command += ["--write", str(Path(directory).resolve())]
    for path in read_only:
        command += ["--read", str(Path(path).resolve())]
    return [*command, "--", *args]


def tool_environment(directory: Path) -> dict[str, str]:
    """A minimal environment: no application configuration or credentials."""
    root = Path(directory).resolve()
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        # TeX may write only relative, non-hidden names below its directory.
        # Reads are not restricted this way: engines and formats legitimately
        # open absolute system paths, so the Landlock ruleset bounds reads.
        "openout_any": "p",
    }
    for name, suffix in (
        ("TEXMFVAR", "var"),
        ("TEXMFCONFIG", "config"),
        ("TEXMFCACHE", "var"),
        ("XDG_CACHE_HOME", "cache"),
        ("TMPDIR", "tmp"),
        ("MAGICK_TEMPORARY_PATH", "tmp"),
    ):
        path = root / ".tex-cache" / suffix
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment[name] = str(path)
    return environment


def _handled_access(abi: int) -> int:
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= REFER
    if abi >= 3:
        handled |= TRUNCATE
    if abi >= 5:
        handled |= IOCTL_DEV
    return handled


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


def _path_beneath(access: int, fd: int) -> ctypes.Array:
    """The kernel's packed 12-byte landlock_path_beneath_attr."""
    return ctypes.create_string_buffer(struct.pack("=Qi", access, fd), 12)


def restrict(writable: list[str], readable: list[str]) -> None:
    """Apply the ruleset to this process. Raises OSError if unavailable."""
    if platform.system() != "Linux":
        raise OSError("Landlock requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(
        LANDLOCK_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION
    )
    if abi < 1:
        raise OSError(ctypes.get_errno(), "Landlock is unavailable")
    handled = _handled_access(abi)
    attr = _RulesetAttr(handled)
    ruleset = libc.syscall(
        LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0
    )
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "Landlock ruleset creation failed")

    def allow(path: str, access: int) -> None:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except FileNotFoundError:
            return
        try:
            if not os.path.isdir(path):
                # Directory-only rights are invalid on files.
                access &= EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV
            rule = _path_beneath(access & handled, fd)
            if (
                libc.syscall(
                    LANDLOCK_ADD_RULE,
                    ruleset,
                    LANDLOCK_RULE_PATH_BENEATH,
                    rule,
                    0,
                )
                != 0
            ):
                raise OSError(ctypes.get_errno(), f"Landlock rule failed: {path}")
        finally:
            os.close(fd)

    try:
        for path in SYSTEM_READ_ONLY:
            allow(path, READ_ONLY)
        for path in DEVICES:
            allow(path, READ_FILE | WRITE_FILE)
        for path in readable:
            allow(path, READ_ONLY)
        for path in writable:
            allow(path, handled)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "no_new_privs failed")
        if libc.syscall(LANDLOCK_RESTRICT_SELF, ruleset, 0) != 0:
            raise OSError(ctypes.get_errno(), "Landlock restriction failed")
    finally:
        os.close(ruleset)


def main(argv: list[str]) -> int:
    writable: list[str] = []
    readable: list[str] = []
    index = 0
    while index < len(argv) and argv[index] != "--":
        flag, value = argv[index], argv[index + 1]
        if flag == "--write":
            writable.append(value)
        elif flag == "--read":
            readable.append(value)
        else:
            print(f"latex_sandbox: unknown option {flag}", file=sys.stderr)
            return UNAVAILABLE
        index += 2
    command = argv[index + 1 :]
    if not writable or not command:
        print("latex_sandbox: missing directory or command", file=sys.stderr)
        return UNAVAILABLE
    try:
        restrict(writable, readable)
    except OSError as error:
        # Fail closed: never run a LaTeX tool unconfined.
        print(f"latex_sandbox: confinement unavailable: {error}", file=sys.stderr)
        return UNAVAILABLE
    try:
        os.execvp(command[0], command)
    except OSError as error:
        print(f"latex_sandbox: {command[0]}: {error.strerror}", file=sys.stderr)
        return UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
