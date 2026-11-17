"""Descriptor-bound startup without procfs or threaded-runtime preexec callbacks."""

from __future__ import annotations

import os
import sys


def descriptor_cwd(fd: int) -> str:
    # Darwin enters the validated directory in the auxiliary process instead.
    return "/" if sys.platform == "darwin" else f"/proc/self/fd/{fd}"


def descriptor_argv(argv: list[str], fd: int) -> list[str]:
    if sys.platform != "darwin":
        return argv
    # Isolated Python ignores user startup code, site packages and PYTHON* settings.
    return [sys.executable, "-I", "-S", os.path.abspath(__file__), str(fd), *argv]


if __name__ == "__main__":
    directory_fd = int(sys.argv[1])
    command = sys.argv[2:]
    os.fchdir(directory_fd)
    os.close(directory_fd)
    os.execve(command[0], command, os.environ)
