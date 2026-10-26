"""Unit tests for ``scripts.apply_patches``."""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.apply_patches import (
    _clean_git_env,
    apply_all_patches,
    apply_single_patch,
    can_apply_patch,
    is_patch_applied,
    main,
    parse_args,
)


def _init_git_repo(repo_dir: Path) -> None:
    """Initialize a temporary git repository with a sample tracked file.

    Args:
        repo_dir: Directory path where the repository will be created.
    """
    nl = chr(10)
    git_env = _clean_git_env()
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True, env=git_env)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"],
        check=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "Test User"],
        check=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "commit.gpgsign", "false"],
        check=True,
        env=git_env,
    )
    target = repo_dir / "hello.txt"
    target.write_text(f"line1{nl}line2{nl}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "hello.txt"], check=True, env=git_env)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-q", "-m", "initial"],
        check=True,
        env=git_env,
    )


def test_patch_lifecycle_and_idempotency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify forward application, reverse idempotency check, and stamp output.

    Args:
        tmp_path: Pytest temporary directory fixture.
        capsys: Pytest stdout/stderr capture fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "hijack_git_dir"))
    assert "GIT_DIR" not in _clean_git_env()

    nl = chr(10)
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    patch_file = patches_dir / "0001-update-hello.patch"
    patch_file.write_text(
        nl.join(
            [
                "--- a/hello.txt",
                "+++ b/hello.txt",
                "@@ -1,2 +1,2 @@",
                " line1",
                "-line2",
                "+line2_patched",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert not is_patch_applied(repo_dir, patch_file)
    assert can_apply_patch(repo_dir, patch_file)

    stamp_file = tmp_path / "stamps" / "patched.stamp"
    args = parse_args([str(repo_dir), str(patches_dir), "--stamp", str(stamp_file)])
    assert args.busybox_dir == repo_dir
    assert args.patches_dir == patches_dir
    assert args.stamp_file == stamp_file

    # First run applies the patch and writes the stamp file
    rc1 = main([str(repo_dir), str(patches_dir), "--stamp", str(stamp_file)])
    assert rc1 == 0
    assert "1 newly applied" in capsys.readouterr().out
    assert stamp_file.read_text(encoding="utf-8") == f"0001-update-hello.patch{nl}"
    assert is_patch_applied(repo_dir, patch_file)

    # Second run detects the patch is already applied and is a no-op
    rc2 = main([str(repo_dir), str(patches_dir), "--stamp", str(stamp_file)])
    assert rc2 == 0
    assert "0 newly applied" in capsys.readouterr().out
    assert stamp_file.read_text(encoding="utf-8") == ""

    # Run without --stamp
    rc3 = main([str(repo_dir), str(patches_dir)])
    assert rc3 == 0
    capsys.readouterr()


def test_patch_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify error handling for missing directories and corrupt patches.

    Args:
        tmp_path: Pytest temporary directory fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    nl = chr(10)
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        apply_all_patches(tmp_path / "missing_repo", patches_dir)

    with pytest.raises(FileNotFoundError):
        apply_all_patches(repo_dir, tmp_path / "missing_patches")

    bad_patch = patches_dir / "0001-conflict.patch"
    bad_patch.write_text(
        nl.join(
            [
                "--- a/hello.txt",
                "+++ b/hello.txt",
                "@@ -1,2 +1,2 @@",
                " non_matching_context",
                "-foo",
                "+bar",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError):
        apply_single_patch(repo_dir, bad_patch)

    rc = main([str(repo_dir), str(patches_dir)])
    assert rc == 1
    assert "Error applying patches:" in capsys.readouterr().err

    old_argv = sys.argv
    sys.argv = ["apply_patches.py", str(repo_dir), str(patches_dir)]
    try:
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "apply_patches.py"
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(script_path), run_name="__main__")
        assert exc_info.value.code == 1
    finally:
        sys.argv = old_argv
    capsys.readouterr()
