import os
import shutil
import subprocess
import sys

import matoloop_version
from matoloop_version import source_version


def test_source_version_is_branch_independent_and_survives_packaging(tmp_path, monkeypatch):
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "Version Test")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "test@example.invalid")
        monkeypatch.setenv(f"GIT_{name}_DATE", "2026-10-08T00:30:00+02:00")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-b", "develop")
    (tmp_path / "source").write_text("original")
    git("add", ".")
    git("commit", "-m", "Initial")
    expected = f"2026-10-07-{git('rev-parse', 'HEAD')[:8]}"
    assert source_version(tmp_path) == expected
    git("switch", "-c", "main")
    assert source_version(tmp_path) == expected
    (tmp_path / "source").write_text("changed")
    assert source_version(tmp_path) == expected + "-dirty"

    installed = tmp_path / "installed/lib/python/site-packages/matoloop_version"
    installed.mkdir(parents=True)
    shutil.copy2(matoloop_version.__file__, installed / "__init__.py")
    (installed / "build-version.txt").write_text(expected + "\n")
    result = subprocess.check_output(
        [sys.executable, "-c", "from matoloop_version import get_version; print(get_version())"],
        cwd=installed.parent,
        env={**os.environ, "PATH": "", "PYTHONPATH": str(installed.parent)}, text=True,
    )
    assert result.strip() == expected
