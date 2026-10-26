"""Unit tests for ``scripts.run_applet_test``."""

from __future__ import annotations

import runpy
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from scripts.run_applet_test import (
    _query_udp_with_retry,
    _wait_tcp_ready,
    assert_ok,
    main,
    parse_args,
    run_bb,
)


def _resolve_busybox_binary(
    candidates: Sequence[Path],
    fallback_builder: Callable[[], Path],
) -> Path:
    """Return the first existing candidate binary or invoke ``fallback_builder``.

    Args:
        candidates: Ordered sequence of candidate binary paths to check.
        fallback_builder: Callable that builds and returns a binary path if no
            candidate exists.

    Returns:
        Path to an existing ``busybox`` executable.
    """
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return fallback_builder()


def _build_busybox_in_dir(repo_root: Path, build_dir: Path, exe_name: str) -> Path:
    """Configure and compile the ``busybox`` target in ``build_dir``.

    Args:
        repo_root: Path to the repository root containing ``CMakeLists.txt``.
        build_dir: CMake binary directory to configure and build.
        exe_name: Expected output binary filename (``busybox`` or ``busybox.exe``).

    Returns:
        Path to the compiled binary inside ``build_dir``.
    """
    subprocess.run(
        ["cmake", "-B", str(build_dir), "-S", str(repo_root), "-DBUILD_TESTING=OFF"],
        check=True,
    )
    subprocess.run(
        ["cmake", "--build", str(build_dir), "--target", "busybox", "-j", "4"],
        check=True,
    )
    return build_dir / exe_name


def _find_compiled_busybox() -> Path:
    """Locate or build the compiled ``busybox`` binary for functional tests.

    Returns:
        Path to the compiled ``busybox`` binary.
    """
    repo_root = Path(__file__).resolve().parent.parent
    candidates = (
        repo_root / "build_clang" / "busybox",
        repo_root / "build_gcc" / "busybox",
        repo_root / "build_test" / "busybox",
        repo_root / "build_test" / "busybox.exe",
        repo_root / "build_test" / "Release" / "busybox.exe",
        repo_root / "build" / "busybox",
        repo_root / "build" / "busybox.exe",
        repo_root / "build" / "Release" / "busybox.exe",
    )
    return _resolve_busybox_binary(
        candidates,
        lambda: _build_busybox_in_dir(repo_root, repo_root / "build_clang", "busybox"),
    )


def test_resolve_and_build_busybox_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ``_resolve_busybox_binary`` and ``_build_busybox_in_dir`` branches.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    existing = tmp_path / "busybox"
    existing.write_text("bin", encoding="utf-8")
    missing = tmp_path / "missing"

    assert _resolve_busybox_binary([missing, existing], lambda: missing) == existing
    assert _resolve_busybox_binary([missing], lambda: existing) == existing

    recorded_cmds: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[bytes]:
        """Record subprocess commands without invoking CMake.

        Args:
            cmd: Command argument list.
            check: Whether non-zero exit raises CalledProcessError.

        Returns:
            Dummy CompletedProcess instance.
        """
        assert check
        recorded_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    built = _build_busybox_in_dir(tmp_path, tmp_path / "bdir", "busybox")
    assert built == tmp_path / "bdir" / "busybox"
    assert len(recorded_cmds) == 2


def test_assert_ok_success_and_failures() -> None:
    """Verify ``assert_ok`` validates returncode and expected substring."""
    nl = chr(10)
    crlf = chr(13) + chr(10)
    ok_res = subprocess.CompletedProcess(
        args=["busybox", "echo", "hello"],
        returncode=0,
        stdout=f"hello{crlf}".encode(),
        stderr=b"",
    )
    assert assert_ok(ok_res, "hello") == f"hello{nl}"
    assert assert_ok(ok_res) == f"hello{nl}"

    with pytest.raises(AssertionError, match="Expected 'missing'"):
        assert_ok(ok_res, "missing")

    fail_res = subprocess.CompletedProcess(
        args=["busybox", "false"],
        returncode=1,
        stdout=b"",
        stderr=b"err",
    )
    with pytest.raises(AssertionError, match="failed with exit code 1"):
        assert_ok(fail_res)


def test_parse_args_and_main_error_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify CLI argument parsing, missing binary error, and suite failure handling.

    Args:
        tmp_path: Pytest temporary directory fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    nl = chr(10)
    missing_bin = tmp_path / "nonexistent_busybox"
    parsed = parse_args([str(missing_bin), "--suite", "coreutils"])
    assert parsed.busybox == missing_bin
    assert parsed.suite == "coreutils"

    assert main([str(missing_bin)]) == 1
    assert "busybox binary not found" in capsys.readouterr().err

    failing_bin = tmp_path / "failing_busybox"
    failing_bin.write_text(f"#!/bin/sh{nl}exit 1{nl}", encoding="utf-8")
    failing_bin.chmod(0o755)
    assert main([str(failing_bin), "--suite", "coreutils"]) == 1
    assert "Applet test suite 'coreutils' failed:" in capsys.readouterr().err

    old_argv = sys.argv
    sys.argv = ["run_applet_test.py", str(missing_bin)]
    try:
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_applet_test.py"
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(script_path), run_name="__main__")
        assert exc_info.value.code == 1
    finally:
        sys.argv = old_argv
    capsys.readouterr()


def test_wait_tcp_and_udp_retry_error_branches() -> None:
    """Verify ``_wait_tcp_ready`` and ``_query_udp_with_retry`` retry and timeout paths."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(OSError):
        _wait_tcp_ready(closed_port, timeout=0.08)

    udp_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_probe.bind(("127.0.0.1", 0))
    unused_udp_port = udp_probe.getsockname()[1]
    udp_probe.close()

    with pytest.raises(OSError):
        _query_udp_with_retry(unused_udp_port, b"ping", retries=2)


def test_run_all_suites_against_compiled_busybox(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run all functional applet test suites through ``main`` for 100% coverage.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    bb = _find_compiled_busybox()
    res = run_bb(bb, ["echo", "probe"])
    assert res.returncode == 0
    assert main([str(bb), "--suite", "all"]) == 0
    assert "passed" in capsys.readouterr().out
