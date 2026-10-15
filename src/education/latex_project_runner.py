"""Resource-limited launcher for the fixed project HTML conversion command."""

import os
import ctypes
from pathlib import Path
import resource
import signal
import sys

# pandoc's runtime reserves well over 1 GiB of address space on x86-64 and
# fails to start below that, so the bound sits at the worker's memory limit.
ADDRESS_SPACE_LIMIT = 4 * 1024 * 1024 * 1024


def main():
    if len(sys.argv) != 4:
        raise SystemExit(2)
    executable, source, parent_pid = sys.argv[1:]
    if not Path(executable).is_absolute() or not Path(source).is_absolute():
        raise SystemExit(2)
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform == "linux":
        # The scan worker may be cancelled while this child owns a process
        # group. Bind its lifetime to that worker as well as its CPU/wall limits.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != int(
            parent_pid
        ):
            raise SystemExit(2)
        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_LIMIT,) * 2)
        # Beyond pandoc's own sandbox, the process can read only system files
        # and this analysis directory; output goes to the inherited stdout.
        from latex_sandbox import restrict

        try:
            restrict([], [str(Path(source).parent)])
        except OSError:
            raise SystemExit(2)
    # No filters, PDF engines, resource embedding, templates or user flags.
    # Pandoc's reader may only read this pre-resolved analysis file.
    os.execve(
        executable,
        [
            executable,
            "--sandbox",
            "--verbose",
            "--from=latex",
            "--to=html5",
            "--mathml",
            source,
        ],
        {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(Path(source).parent)},
    )


if __name__ == "__main__":
    main()
