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

import contextlib
import ctypes
import os
import platform
import shutil
import struct
import sys
import tempfile
from collections.abc import Iterator
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

# ImageMagick 7.1.1-43 unwraps a gzip (1F 8B 08) or bzip2 ("BZh") file by its
# leading bytes in OpenBlob (MagickCore/blob.c), before it chooses a coder or
# applies its policy, so the policy cannot refuse such a file. LaTeXML starts
# ImageMagick on any file a document names. Files starting with these wrappers
# are therefore refused before LaTeXML runs. That ImageMagick does not unwrap xz
# or zstd; both are refused anyway, so a later version that does is covered.
COMPRESSION_MAGICS = (
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
)
MAX_SCANNED_FILES = 4096


def compression_wrapper(head: bytes) -> str | None:
    """The compression wrapper ``head`` starts with, or None."""
    for magic, name in COMPRESSION_MAGICS:
        if head.startswith(magic):
            return name
    return None


def find_compressed_file(directories) -> Path | None:
    """The first file under ``directories`` that starts with a compression wrapper.

    Every file is checked, whatever its name, because ImageMagick identifies a
    file by its bytes. A link to a file is checked through the link; a link to
    a directory is not entered. The scan fails closed: an unreadable entry, or
    a tree with more than MAX_SCANNED_FILES files, is returned as the finding.
    """
    seen: set[Path] = set()
    count = 0
    pending = []
    for directory in directories:
        root = Path(directory).resolve()
        if root not in seen:
            seen.add(root)
            pending.append(root)
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return directory
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=True):
                    continue
                count += 1
                if count > MAX_SCANNED_FILES:
                    return path
                with open(path, "rb") as handle:
                    if compression_wrapper(handle.read(8)):
                        return path
            except OSError:
                return path
    return None


def confined(
    args: list[str], directory: Path, read_only: tuple[Path, ...] = ()
) -> list[str]:
    """Return a command that runs ``args`` confined to ``directory``."""
    command = [sys.executable, "-I", str(Path(__file__).resolve())]
    command += ["--write", str(Path(directory).resolve())]
    for path in read_only:
        command += ["--read", str(Path(path).resolve())]
    return [*command, "--", *args]


@contextlib.contextmanager
def tool_home(directory: Path) -> Iterator[Path]:
    """A new, empty home directory for one tool run, removed afterwards.

    Tools read configuration from their home directory. ImageMagick, for one,
    adds $HOME/.config/ImageMagick/policy.xml after the system policy, so a
    rule there would override the image's policy. The home is therefore never
    the conversion directory: it is created outside it, and the Landlock
    ruleset grants a confined tool neither read nor write access to it, so a
    document cannot place configuration there.
    """
    root = Path(directory).resolve()
    for parent in _home_parents():
        parent = Path(parent).resolve()
        if parent.is_relative_to(root) or not os.access(parent, os.W_OK | os.X_OK):
            continue
        home = Path(tempfile.mkdtemp(prefix="aelira-tool-home-", dir=parent))
        break
    else:
        raise OSError("no directory outside the conversion directory for a tool home")
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _home_parents() -> tuple[str, ...]:
    """Where a tool home may be created, in order of preference.

    The system temporary directory comes first. A conversion directory can
    itself be that directory, so shared-memory and persistent temporary
    directories follow. None of them is readable by a confined tool.
    """
    return (tempfile.gettempdir(), "/dev/shm", "/var/tmp")


def tool_environment(directory: Path, home: Path) -> dict[str, str]:
    """A minimal environment: no application configuration or credentials.

    ``home`` comes from ``tool_home`` and is never the conversion directory.
    MAGICK_CONFIGURE_PATH stays unset: ImageMagick reads it as well as, not
    instead of, the system configuration, so it could only add rules.
    """
    root = Path(directory).resolve()
    home = Path(home).resolve()
    if home.is_relative_to(root):
        raise OSError("tool home would be inside the conversion directory")
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        # TeX may write only relative, non-hidden names below its directory.
        # Reads are not restricted this way: engines and formats legitimately
        # open absolute system paths, so the Landlock ruleset bounds reads.
        "openout_any": "p",
        # ImageMagick 7.1.1-43 sets no time limit by default and applies the
        # policy's time value only as a cap on a limit set another way, so
        # the 50-second limit takes effect only through this variable. It
        # stays below the 60-second post-processing timeout.
        "MAGICK_TIME_LIMIT": "50",
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
