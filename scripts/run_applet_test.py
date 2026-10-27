#!/usr/bin/env python3
"""Functional test driver for BusyBox applets under CTest.

Provides isolated temporary workspaces, deterministic fixture generation,
loopback TCP/UDP mock servers (HTTP, FTP, DNS, Telnet, Netcat), and output
verification so every BusyBox applet in ``applets_manifest.py`` is tested
against real functional inputs and expected outputs rather than ``--help``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import lzma
import os
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path

NL = chr(10)
CR = chr(13)
CRLF = CR + NL
TAB = chr(9)
NUL_BYTE = bytes([0])


def _bb_cmd(busybox: Path, args: Sequence[str]) -> list[str]:
    """Build the command list for invoking ``busybox`` (including ``wine`` if needed).

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        args: Applet name and arguments to pass to ``busybox``.

    Returns:
        Command list ready for ``subprocess.run`` or ``subprocess.Popen``.
    """
    resolved = str(busybox.resolve())
    return (
        ["wine", resolved, *args]
        if resolved.endswith(".exe") and sys.platform != "win32"
        else [resolved, *args]
    )


def run_bb(
    busybox: Path,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    stdin_data: bytes | None = None,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[bytes]:
    """Invoke the compiled BusyBox binary with the given arguments.

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        args: Applet name and arguments to pass to ``busybox``.
        cwd: Optional working directory for the subprocess.
        stdin_data: Optional bytes piped to standard input.
        timeout: Maximum execution time in seconds.

    Returns:
        ``subprocess.CompletedProcess`` containing ``returncode``, ``stdout``,
        and ``stderr``.
    """
    return subprocess.run(
        _bb_cmd(busybox, args),
        cwd=str(cwd) if cwd is not None else None,
        input=stdin_data,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def assert_ok(
    res: subprocess.CompletedProcess[bytes],
    expected_substr: str | None = None,
) -> str:
    """Assert that a BusyBox invocation exited with 0 and optional output match.

    Args:
        res: Completed subprocess result.
        expected_substr: Optional substring that must appear in decoded stdout.

    Returns:
        Decoded standard output string (with normalized LF line endings).

    Raises:
        AssertionError: If the return code is non-zero or ``expected_substr``
            is missing from stdout.
    """
    stdout_text = res.stdout.decode("utf-8", errors="replace").replace(CRLF, NL)
    stderr_text = res.stderr.decode("utf-8", errors="replace").replace(CRLF, NL)
    if res.returncode != 0:
        raise AssertionError(
            f"Command {res.args!r} failed with exit code {res.returncode}."
            f" stdout={stdout_text!r} stderr={stderr_text!r}"
        )
    if expected_substr is not None and expected_substr not in stdout_text:
        raise AssertionError(
            f"Expected {expected_substr!r} in stdout of {res.args!r}, got: {stdout_text!r}"
        )
    return stdout_text


def _wait_tcp_ready(port: int, timeout: float = 5.0) -> None:
    """Poll ``127.0.0.1:port`` until a TCP listener accepts connections.

    Args:
        port: Loopback TCP port number to probe.
        timeout: Maximum total wait time in seconds.

    Raises:
        OSError: If the TCP listener does not become ready before ``timeout``.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _query_udp_with_retry(port: int, query: bytes, retries: int = 10) -> bytes:
    """Send a UDP datagram to ``127.0.0.1:port`` with retries for startup delay.

    Args:
        port: Loopback UDP port number to query.
        query: Raw UDP request payload bytes.
        retries: Maximum number of transmission attempts.

    Returns:
        Raw UDP response payload bytes.

    Raises:
        OSError: If all UDP transmission attempts time out or fail.
    """
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.settimeout(0.5)
    try:
        attempt = 0
        while True:
            udp_sock.sendto(query, ("127.0.0.1", port))
            try:
                resp, _ = udp_sock.recvfrom(512)
                return resp
            except OSError:
                attempt += 1
                if attempt >= retries:
                    raise
                time.sleep(0.1)
    finally:
        udp_sock.close()


def run_coreutils_suite(busybox: Path, work: Path) -> None:
    """Verify coreutils applets with functional inputs and output assertions.

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        work: Isolated temporary directory for test fixtures.
    """
    assert_ok(run_bb(busybox, ["true"]))
    assert run_bb(busybox, ["false"]).returncode != 0
    assert_ok(run_bb(busybox, ["echo", "hello", "world"]), "hello world")
    assert_ok(run_bb(busybox, ["echo"]), NL)
    assert_ok(run_bb(busybox, ["printf", f"%s=%d{NL}", "ans", "42"]), "ans=42")
    assert_ok(run_bb(busybox, ["test", "5", "-eq", "5"]))
    assert_ok(run_bb(busybox, ["[", "abc", "=", "abc", "]"]))
    assert_ok(run_bb(busybox, ["[[", "abc", "=", "abc", "]]"]))
    assert_ok(run_bb(busybox, ["basename", "/usr/local/bin/foo.txt", ".txt"]), "foo")
    assert_ok(run_bb(busybox, ["basename", "/usr/local/bin/foo///"]), "foo")
    assert_ok(run_bb(busybox, ["basename", "/"]), "/")
    assert_ok(run_bb(busybox, ["basename", "/abc"]), "abc")
    assert_ok(run_bb(busybox, ["basename", "foo.txt", "foo.txt"]), "foo.txt")
    assert_ok(run_bb(busybox, ["basename", "foo.txt", ".log"]), "foo.txt")
    assert_ok(run_bb(busybox, ["basename", "-a", "/a/b", "/c/d"]), f"b{NL}d")
    assert_ok(run_bb(busybox, ["basename", "-s", ".txt", "x.txt", "y.txt"]), f"x{NL}y")
    assert run_bb(busybox, ["basename", "a", "b", "c"]).returncode != 0
    ro_file = work / "ro_sink.txt"
    ro_file.write_bytes(b"x")
    ro_fd = os.open(str(ro_file), os.O_RDONLY)
    wo_fd = os.open(str(ro_file), os.O_WRONLY)
    try:
        for ro_args in (
            ["echo", "closed_stdout"],
            ["basename", "foo"],
            ["yes"],
            ["yes", "xyz", "abc"],
            ["seq", "1", "10000"],
        ):
            subprocess.run(
                _bb_cmd(busybox, ro_args),
                stdout=ro_fd,
                stderr=subprocess.PIPE,
                check=False,
            )
        subprocess.run(
            _bb_cmd(busybox, ["cksum"]),
            input=b"abc",
            stdout=ro_fd,
            stderr=subprocess.PIPE,
            check=False,
        )
        subprocess.run(
            _bb_cmd(busybox, ["tee"]),
            input=b"abc\n",
            stdout=ro_fd,
            stderr=subprocess.PIPE,
            check=False,
        )
        subprocess.run(
            _bb_cmd(busybox, ["cat"]),
            stdin=wo_fd,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            _bb_cmd(busybox, ["sum"]),
            stdin=wo_fd,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            _bb_cmd(busybox, ["md5sum"]),
            stdin=wo_fd,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            _bb_cmd(busybox, ["uuencode", "sample.txt"]),
            stdin=wo_fd,
            capture_output=True,
            check=False,
        )
    finally:
        os.close(ro_fd)
        os.close(wo_fd)
    assert_ok(run_bb(busybox, ["dirname", "/usr/local/bin/foo.txt"]), "/usr/local/bin")
    assert_ok(run_bb(busybox, ["dirname", "--", "/usr/local/bin/foo.txt"]), "/usr/local/bin")
    assert_ok(run_bb(busybox, ["dirname", "-a/b"]), "-a")
    assert_ok(run_bb(busybox, ["dirname", "--x/b"]), "--x")
    assert run_bb(busybox, ["dirname"]).returncode != 0
    assert run_bb(busybox, ["dirname", "a", "b"]).returncode != 0
    assert_ok(run_bb(busybox, ["seq", "1", "3"]), f"1{NL}2{NL}3")
    assert_ok(run_bb(busybox, ["seq", "3"]), f"1{NL}2{NL}3")
    assert_ok(run_bb(busybox, ["seq", "-w", "-s", ":", "-1", "1"]), "-1:00:01")
    assert_ok(run_bb(busybox, ["seq", "-.5", "0.5", ".5"]), "-0.5")
    assert_ok(run_bb(busybox, ["seq", "1.", "2"]), "1")
    assert_ok(run_bb(busybox, ["seq", "2", "-1", "1"]), f"2{NL}1")
    assert_ok(run_bb(busybox, ["seq", "5", "1"]))
    assert run_bb(busybox, ["seq"]).returncode != 0
    assert run_bb(busybox, ["seq", "1", "2", "3", "4"]).returncode != 0
    assert run_bb(busybox, ["seq", "-,"]).returncode != 0
    assert run_bb(busybox, ["seq", "1bad"]).returncode != 0
    assert_ok(
        run_bb(
            busybox,
            [
                "factor",
                "+1",
                "4",
                "9",
                "45",
                "75",
                "1267",
                "10891",
                "14411",
                "6000011",
                "250000",
                "42",
            ],
        ),
        "42: 2 3 7",
    )
    assert_ok(
        run_bb(busybox, ["factor"], stdin_data=f"  12   25{NL}   {NL}".encode()),
        "12: 2 2 3",
    )
    assert run_bb(busybox, ["factor", "bad_num"]).returncode != 0
    assert_ok(run_bb(busybox, ["cal", "1", "2026"]), "2026")
    assert_ok(run_bb(busybox, ["date", "-u", "+%Y"]), "20")
    for date_iso in ("-I", "-Idate", "-Id", "-Iseconds", "-Is"):
        assert_ok(run_bb(busybox, ["date", date_iso]))
    assert run_bb(busybox, ["date", "-Ix"]).returncode != 0
    assert_ok(run_bb(busybox, ["env"]), "=")
    assert_ok(run_bb(busybox, ["env", "-0", "FOO=bar"]), "FOO=bar")
    assert_ok(run_bb(busybox, ["env", "-", "FOO=bar"]), "FOO=bar")
    assert_ok(run_bb(busybox, ["env", "--", "-x=1"]), "-x=1")
    assert_ok(
        run_bb(busybox, ["env", "-i", "-u", "OLD_VAR", "A=1", "echo", "env_cmd_ok"]),
        "env_cmd_ok",
    )
    subprocess.run(
        _bb_cmd(busybox, ["env"]),
        env={},
        capture_output=True,
        check=True,
    )
    assert run_bb(busybox, ["env", "nonexistent_cmd_xyz_987"]).returncode == 127
    assert run_bb(busybox, ["env", str(work)]).returncode == 126
    assert_ok(run_bb(busybox, ["printenv"]))
    assert_ok(run_bb(busybox, ["printenv", "PATH"]))
    assert_ok(run_bb(busybox, ["env", "-i", "printenv"]))
    assert run_bb(busybox, ["printenv", "PATH", "NONEXISTENT_VAR_XYZ_987"]).returncode == 1
    assert_ok(run_bb(busybox, ["pwd"]))
    deep_pwd = work / ("d" * 45) / ("e" * 45)
    deep_pwd.mkdir(parents=True, exist_ok=True)
    assert_ok(run_bb(busybox, ["pwd", "-P"], cwd=deep_pwd))
    assert_ok(run_bb(busybox, ["id"]), "uid=")
    assert_ok(run_bb(busybox, ["whoami"]))
    assert run_bb(busybox, ["whoami", "extra"]).returncode != 0
    assert_ok(run_bb(busybox, ["uname"]))
    assert_ok(run_bb(busybox, ["uname", "-m"]))
    assert_ok(run_bb(busybox, ["uname", "-a"]))
    assert run_bb(busybox, ["uname", "extra"]).returncode != 0
    assert_ok(run_bb(busybox, ["sleep", "0"]))
    assert_ok(run_bb(busybox, ["sleep", "2147483647"]))
    assert_ok(run_bb(busybox, ["sleep", "inf"]))
    assert run_bb(busybox, ["sleep"]).returncode != 0
    assert run_bb(busybox, ["sleep", "0.1.2"]).returncode != 0
    assert run_bb(busybox, ["sleep", "5000000000"]).returncode != 0
    assert_ok(run_bb(busybox, ["usleep", "1"]))
    assert run_bb(busybox, ["usleep"]).returncode != 0
    assert_ok(run_bb(busybox, ["sync"]))
    assert_ok(run_bb(busybox, ["sync", "ignored_arg"]))
    assert_ok(run_bb(busybox, ["clear"]))
    subprocess.run(
        _bb_cmd(busybox, ["reset"]),
        capture_output=True,
        env={"PATH": ""},
        check=True,
    )

    proc = subprocess.Popen(
        _bb_cmd(busybox, ["yes", "xyz"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        chunk = proc.stdout.read(16) if proc.stdout is not None else b""
    finally:
        proc.kill()
        proc.wait()
    assert b"xyz" in chunk

    sample = work / "sample.txt"
    sample.write_text(f"beta{NL}alpha{NL}alpha{NL}gamma{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["cat", str(sample)]), f"beta{NL}alpha")
    assert_ok(run_bb(busybox, ["cat", str(sample), str(sample)]), f"beta{NL}alpha")
    assert_ok(run_bb(busybox, ["cat"], stdin_data=f"stdin_cat{NL}".encode()), "stdin_cat")
    assert_ok(run_bb(busybox, ["cat", "-n"], stdin_data=f"l1{NL}{NL}l2{NL}".encode()), "l1")
    assert_ok(run_bb(busybox, ["cat", "-b"], stdin_data=f"l1{NL}{NL}l2{NL}".encode()), "l1")
    assert_ok(run_bb(busybox, ["cat", "-b", str(sample), str(sample)]), "beta")
    assert run_bb(busybox, ["cat", "-n", str(work / "missing_cat.txt")]).returncode != 0
    assert run_bb(busybox, ["cat", str(work / "missing_cat.txt")]).returncode != 0
    assert run_bb(busybox, ["cat", str(work)]).returncode != 0
    assert_ok(run_bb(busybox, ["head", "-n", "2", str(sample)]), f"beta{NL}alpha")
    assert_ok(run_bb(busybox, ["tail", "-n", "1", str(sample)]), "gamma")
    assert_ok(run_bb(busybox, ["wc", "-l", str(sample)]), "4")
    wc_special = work / "wc_special.txt"
    wc_special.write_bytes(b"a b\tc\vd\fe\rf\x01\x1fg\n")
    wc_short = work / "wc_short.txt"
    wc_short.write_bytes(b"x\n")
    assert_ok(run_bb(busybox, ["wc"], stdin_data=b"hello world\n"), "1")
    assert_ok(
        run_bb(busybox, ["wc", "-cmlwL", str(wc_short), str(wc_special), str(wc_short)]),
        "total",
    )
    assert run_bb(busybox, ["wc", str(work / "missing_wc.txt")]).returncode != 0
    assert run_bb(busybox, ["wc", str(work)]).returncode != 0
    assert_ok(run_bb(busybox, ["nl", str(sample)]), f"1{TAB}beta")
    assert_ok(run_bb(busybox, ["nl", "-ba", str(sample), str(sample)]), f"1{TAB}beta")
    assert_ok(
        run_bb(busybox, ["nl", "-bt"], stdin_data=f"line1{NL}{NL}line2{NL}".encode()),
        f"1{TAB}line1",
    )
    assert_ok(run_bb(busybox, ["nl", "-bn"], stdin_data=f"unnumbered{NL}".encode()), "unnumbered")
    assert run_bb(busybox, ["nl", str(work / "missing_nl.txt")]).returncode != 0
    assert_ok(
        run_bb(busybox, ["sort", str(sample)]),
        f"alpha{NL}alpha{NL}beta{NL}gamma",
    )
    assert_ok(
        run_bb(
            busybox,
            ["sort", "-u", str(sample)],
        ),
        "alpha",
    )
    assert_ok(
        run_bb(busybox, ["sort", "-n", "-r", "-b", "-s", "-u", "-z", str(sample), str(sample)]),
    )
    assert_ok(
        run_bb(busybox, ["sort", "-s"], stdin_data=f"same{NL}same{NL}diff{NL}".encode()),
        "diff",
    )
    assert run_bb(busybox, ["sort", "--", "-missing_sort.txt"], cwd=work).returncode != 0
    sorted_file = work / "sorted.txt"
    sorted_file.write_text(f"alpha{NL}alpha{NL}beta{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["uniq", str(sorted_file)]), f"alpha{NL}beta")
    assert_ok(run_bb(busybox, ["uniq", str(sorted_file), str(work / "uniq_out.txt")]))
    dash_uniq_in = work / "-dash_uniq_in.txt"
    dash_uniq_in.write_text(f"k1 A{NL}k2 a{NL}x{NL}", encoding="utf-8")
    assert_ok(
        run_bb(
            busybox,
            [
                "uniq",
                "-c",
                "-d",
                "-i",
                "-f",
                "1",
                "-s",
                "1",
                "--",
                "-dash_uniq_in.txt",
                "-dash_uniq_out.txt",
            ],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(busybox, ["uniq", "-u", "-s", "5", "-", "-"], stdin_data=f"a{NL}b{NL}b{NL}".encode())
    )
    assert_ok(run_bb(busybox, ["uniq", "-z"], stdin_data=b"a\x00a\x00b\x00"))
    assert run_bb(busybox, ["uniq", str(sorted_file), "b", "c"]).returncode != 0
    assert_ok(run_bb(busybox, ["shuf", "-n", "2", str(sample)]))
    assert_ok(run_bb(busybox, ["shuf", "-n", "100", "-z", "-e", "a", "b", "c"]))
    assert_ok(
        run_bb(
            busybox,
            ["shuf", "-i", "1-5", "-o", str(work / "shuf_out.txt")],
        )
    )
    assert_ok(run_bb(busybox, ["shuf"], stdin_data=f"s1{NL}s2{NL}".encode()))
    for bad_shuf in (
        ["shuf", "-i", "1-5", "extra"],
        ["shuf", "-i", "bad"],
        ["shuf", "-i", "5-1"],
        ["shuf", str(sample), str(sample)],
    ):
        assert run_bb(busybox, bad_shuf).returncode != 0

    csv_file = work / "data.csv"
    csv_file.write_text(f"one:two:three{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["cut", "-d:", "-f2", str(csv_file)]), "two")
    assert_ok(
        run_bb(busybox, ["tr", "a-z", "A-Z"], stdin_data=f"hello{NL}".encode()),
        "HELLO",
    )
    assert_ok(
        run_bb(busybox, ["fold", "-w", "3"], stdin_data=f"abcdef{NL}".encode()),
        f"abc{NL}def",
    )
    fold_in = work / "fold_in.txt"
    fold_in.write_bytes(b"\ba\b\tab\rcdefgh\n" + b"x" * 4100)
    assert_ok(run_bb(busybox, ["fold", "-b", "-3", "--", str(fold_in), str(fold_in)]))
    assert_ok(run_bb(busybox, ["fold", "-bs", "-w", "5", str(fold_in)]))
    assert_ok(run_bb(busybox, ["fold", "-w", "5", str(fold_in)]))
    assert run_bb(busybox, ["fold", "---"]).returncode != 0
    assert_ok(run_bb(busybox, ["fold", "-w", "1"], stdin_data=b"\tab"))
    assert_ok(
        run_bb(busybox, ["fold", "-s", "-w", "5"], stdin_data=b"ab cd ef\nabcde f\nabcdefgh\n")
    )
    assert run_bb(busybox, ["fold", str(work / "missing_fold.txt")]).returncode != 0
    assert run_bb(busybox, ["fold", str(work)]).returncode != 0
    assert_ok(
        run_bb(busybox, ["expand", "-t", "4"], stdin_data=f"{TAB}hi{NL}".encode()),
        "    hi",
    )
    assert_ok(
        run_bb(busybox, ["expand", "-i"], stdin_data=f" {TAB}x{TAB}y{NL}".encode()),
        "x\ty",
    )
    assert_ok(run_bb(busybox, ["expand", str(sample), str(sample)]), "beta")
    assert run_bb(busybox, ["expand", str(work / "missing_expand.txt")]).returncode != 0
    assert run_bb(busybox, ["expand", str(work)]).returncode != 0
    subprocess.run(
        _bb_cmd(busybox, ["expand", str(sample)]),
        preexec_fn=lambda: os.close(0),
        capture_output=True,
        check=False,
    )
    assert_ok(
        run_bb(busybox, ["unexpand", "-a"], stdin_data=f"  {TAB}  hello  world{NL}".encode()),
        "hello",
    )
    assert_ok(
        run_bb(
            busybox,
            ["unexpand", "-t", "4", "-f"],
            stdin_data=f"    hello    world{NL}hello    world{NL}".encode(),
        ),
        "hello",
    )
    f1 = work / "f1.txt"
    f2 = work / "f2.txt"
    f3 = work / "f3_short.txt"
    f4 = work / "f4_three.txt"
    f_empty = work / "f_empty.txt"
    f1.write_text(f"a{NL}b{NL}", encoding="utf-8")
    f2.write_text(f"b{NL}c{NL}", encoding="utf-8")
    f3.write_text(f"only1{NL}", encoding="utf-8")
    f4.write_text(f"l1{NL}l2{NL}l3{NL}l4{NL}", encoding="utf-8")
    f_empty.write_text("", encoding="utf-8")
    assert_ok(run_bb(busybox, ["paste", str(f1), str(f2)]), f"a{TAB}b")
    assert_ok(
        run_bb(
            busybox,
            ["paste", "-d", r"\x41\x4a\x4g\x@\101\777\7z\0\n\z", str(f3), str(f1), str(f2)],
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["paste", "-d", r"\0", str(f1), str(f2)],
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["paste", "-s", "-d", r":\0", str(f4), str(f1), str(f_empty)],
        )
    )
    assert_ok(run_bb(busybox, ["paste"], stdin_data=f"p1{NL}p2{NL}".encode()), "p1")
    assert run_bb(busybox, ["paste", "-d", ""]).returncode != 0
    assert run_bb(busybox, ["paste", str(work / "missing_paste.txt")]).returncode != 0
    assert_ok(run_bb(busybox, ["comm", "-12", str(f1), str(f2)]), "b")
    c1 = work / "comm1.txt"
    c2 = work / "comm2.txt"
    c_empty = work / "comm_empty.txt"
    c1.write_text(f"b{NL}c{NL}", encoding="utf-8")
    c2.write_text(f"a{NL}b{NL}d{NL}e{NL}", encoding="utf-8")
    c_empty.write_text("", encoding="utf-8")
    for comm_flags in ([], ["-1"], ["-2"], ["-3"]):
        assert_ok(run_bb(busybox, ["comm", *comm_flags, str(c1), str(c2)]))
    assert_ok(run_bb(busybox, ["comm", str(c_empty), str(c_empty)]))

    tsort_in = work / "tsort.txt"
    tsort_in.write_text(f"a b{NL}b c{NL}c d{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["tsort", str(tsort_in)]), f"a{NL}b{NL}c{NL}d")
    dash_tsort = work / "-dash_tsort.txt"
    dash_tsort.write_text(f"z m{NL}a m{NL}a a{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["tsort", "-dash_tsort.txt"], cwd=work), "a")
    assert_ok(run_bb(busybox, ["tsort", "-"], stdin_data=b"x y"), f"x{NL}y")
    assert_ok(run_bb(busybox, ["tsort"], stdin_data=f"x y{NL}".encode()), f"x{NL}y")
    assert run_bb(busybox, ["tsort", "a", "b"]).returncode != 0
    assert run_bb(busybox, ["tsort"], stdin_data=f"a b c{NL}".encode()).returncode != 0
    assert run_bb(busybox, ["tsort"], stdin_data=f"a b{NL}b a{NL}".encode()).returncode != 0
    split_dir = work / "split_dir"
    split_dir.mkdir()
    assert_ok(run_bb(busybox, ["split", "-l", "1", str(f1), str(split_dir / "part_")]))
    assert (split_dir / "part_aa").exists()
    assert_ok(run_bb(busybox, ["split", "-l", "2"], cwd=split_dir, stdin_data=b"l1\nl2\nl3_no_nl"))
    split_27 = split_dir / "in27.bin"
    split_27.write_bytes(b"x" * 27)
    assert_ok(run_bb(busybox, ["split", "-a", "2", "-b", "1", str(split_27)], cwd=split_dir))
    assert (
        run_bb(
            busybox,
            ["split", "-a", "1", "-b", "1", str(split_27), str(split_dir / "s1_")],
        ).returncode
        != 0
    )
    assert_ok(run_bb(busybox, ["split", "-b", "2"], cwd=split_dir, stdin_data=b"12345"))
    assert run_bb(busybox, ["split", str(split_27), "p" * 300], cwd=split_dir).returncode != 0
    assert run_bb(busybox, ["split", str(split_dir)], cwd=split_dir).returncode != 0
    assert run_bb(busybox, ["split", "--", "-missing_split.txt"], cwd=work).returncode != 0
    tee_out = work / "tee_out.txt"
    assert_ok(
        run_bb(
            busybox,
            ["tee", str(tee_out)],
            stdin_data=f"tee_payload{NL}".encode(),
        ),
        "tee_payload",
    )
    assert_ok(
        run_bb(
            busybox,
            ["tee", "-a", str(tee_out)],
            stdin_data=f"tee_append{NL}".encode(),
        ),
        "tee_append",
    )
    assert "tee_payload" in tee_out.read_text(encoding="utf-8")
    assert (
        run_bb(
            busybox,
            ["tee", "-i", "-", "--", "-dash_tee.txt", str(work / "no_dir" / "out.txt")],
            cwd=work,
            stdin_data=f"t{NL}".encode(),
        ).returncode
        != 0
    )

    hash_in = work / "hash.txt"
    hash_in.write_bytes(b"abc")
    assert_ok(run_bb(busybox, ["crc32", str(hash_in)]), "352441c2")
    assert_ok(run_bb(busybox, ["crc32"], stdin_data=b"abc"), "352441c2")
    assert_ok(
        run_bb(busybox, ["md5sum", str(hash_in)]),
        "900150983cd24fb0d6963f7d28e17f72",
    )
    assert_ok(
        run_bb(busybox, ["sha1sum", str(hash_in)]),
        "a9993e364706816aba3e25717850c26c9cd0d89d",
    )
    assert_ok(
        run_bb(busybox, ["sha256sum", str(hash_in)]),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
    assert_ok(
        run_bb(busybox, ["sha384sum", str(hash_in)]),
        "cb00753f45a35e8bb5a03d699ac65007272c32ab0eded1631a8b605a43ff5bed",
    )
    assert_ok(
        run_bb(busybox, ["sha512sum", str(hash_in)]),
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a",
    )
    assert_ok(
        run_bb(busybox, ["sha3sum", str(hash_in)]),
        "e642824c3f8cf24ad09234ee7d3c766fc9a3a5168d0c94ad73b46fdf",
    )
    hash_60 = work / "hash60.bin"
    hash_60.write_bytes(b"a" * 60)
    hash_120 = work / "hash120.bin"
    hash_120.write_bytes(b"b" * 120)
    hash_200 = work / "hash200.bin"
    hash_200.write_bytes(b"c" * 200)
    assert_ok(run_bb(busybox, ["md5sum", str(hash_60), str(hash_200)]))
    assert_ok(run_bb(busybox, ["sha512sum", str(hash_120), str(hash_200)]))
    assert_ok(run_bb(busybox, ["sha3sum", str(hash_200)]))
    assert run_bb(busybox, ["md5sum", str(work / "missing_hash.bin")]).returncode != 0
    for bad_sha3 in (["sha3sum", "-a", "0"], ["sha3sum", "-a", "800"], ["sha3sum", "-a", "225"]):
        assert run_bb(busybox, bad_sha3, stdin_data=b"a").returncode != 0
    assert_ok(run_bb(busybox, ["cksum", str(hash_in), str(hash_in)]), "1219131554 3")
    assert run_bb(busybox, ["cksum", str(work / "missing_cksum.txt")]).returncode != 0
    assert run_bb(busybox, ["cksum", str(work)]).returncode != 0
    assert_ok(run_bb(busybox, ["sum", str(hash_in)]), "16556")
    assert_ok(run_bb(busybox, ["sum"], stdin_data=b"abc"), "16556")
    assert_ok(run_bb(busybox, ["sum", "-r", str(hash_in), str(hash_in)]), "16556")
    assert_ok(run_bb(busybox, ["sum", "-s", str(hash_in), str(hash_in)]))
    assert run_bb(busybox, ["sum", str(work / "missing_sum.txt")]).returncode != 0
    assert run_bb(busybox, ["sum", str(work)]).returncode != 0

    b64 = assert_ok(run_bb(busybox, ["base64", str(hash_in)]), "YWJj")
    assert_ok(
        run_bb(busybox, ["base64", "-d"], stdin_data=b64.encode("utf-8")),
        "abc",
    )
    assert_ok(run_bb(busybox, ["base64", "-w", "0"], stdin_data=b"abc"), "YWJj")
    assert_ok(run_bb(busybox, ["base64", "-w", "4"], stdin_data=b"abc"), "YWJj")
    assert_ok(run_bb(busybox, ["base64", "-w", "7"], stdin_data=b"a" * 1000))
    assert run_bb(busybox, ["base64", str(work)]).returncode != 0
    b32 = assert_ok(run_bb(busybox, ["base32", str(hash_in)]), "MFRGG===")
    assert_ok(
        run_bb(busybox, ["base32", "-d"], stdin_data=b32.encode("utf-8")),
        "abc",
    )
    for b32_len in (1, 2, 3, 4, 5):
        assert_ok(run_bb(busybox, ["base32"], stdin_data=b"x" * b32_len))

    uu_encoded = assert_ok(
        run_bb(busybox, ["uuencode", str(hash_in), "decoded.txt"]),
        "begin ",
    )
    assert_ok(
        run_bb(busybox, ["uuencode", "-m", "stdin.txt"], stdin_data=b"abc"),
        "begin-base64 ",
    )
    assert run_bb(busybox, ["uuencode"]).returncode != 0
    assert run_bb(busybox, ["uuencode", "a", "b", "c"]).returncode != 0
    uu_file = work / "encoded.uu"
    uu_file.write_text(uu_encoded, encoding="utf-8")
    uu_out = work / "decoded.txt"
    assert_ok(run_bb(busybox, ["uudecode", "-o", str(uu_out), str(uu_file)]))
    assert uu_out.read_bytes() == b"abc"
    assert_ok(
        run_bb(
            busybox,
            ["uudecode"],
            cwd=work,
            stdin_data=(
                f"preamble{NL}begin 644 -dash_uu.bin\r{NL}"
                f'!80``\r{NL}"86(``{NL}&86)C9&5F{NL}`{NL}end{NL}'
            ).encode(),
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["uudecode"],
            cwd=work,
            stdin_data=f"begin-base64 644 -{NL}YWJj{NL}===={NL}".encode(),
        ),
        "abc",
    )
    assert_ok(
        run_bb(
            busybox,
            ["uudecode"],
            cwd=work,
            stdin_data=f"begin 644 /dev/stdout{NL}#86)C{NL}`{NL}end{NL}".encode(),
        ),
        "abc",
    )
    for bad_uu in (
        f"no_begin_here{NL}",
        f"begin 644{NL}end{NL}",
        f"begin 644 x.bin{NL}",
        f"begin 644 x.bin{NL}{NL}",
        f"begin 644 x.bin{NL}#8{NL}end{NL}",
        f"begin 644 x.bin{NL}N{'a' * 70}{NL}end{NL}",
    ):
        assert run_bb(busybox, ["uudecode"], cwd=work, stdin_data=bad_uu.encode()).returncode != 0
    assert (
        run_bb(
            busybox,
            ["uudecode", "-o", str(work / "no_dir" / "out.bin"), str(uu_file)],
        ).returncode
        != 0
    )
    uu_trim1 = work / "trim1.uu"
    uu_trim1.write_text(f"begin 644   {TAB}trim_a.txt  {NL}#86)C{NL}`{NL}end{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["uudecode", str(uu_trim1)], cwd=work))
    assert (work / "trim_a.txt").read_bytes() == b"abc"
    uu_trim2 = work / "trim2.uu"
    uu_trim2.write_text(f"begin 644 trim_b.txt{NL}#86)C{NL}`{NL}end{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["uudecode", str(uu_trim2)], cwd=work))
    assert (work / "trim_b.txt").read_bytes() == b"abc"
    uu_trim3 = work / "trim3.uu"
    uu_trim3.write_text(f"begin 644    {NL}#86)C{NL}`{NL}end{NL}", encoding="utf-8")
    run_bb(busybox, ["uudecode", str(uu_trim3)], cwd=work)

    assert_ok(run_bb(busybox, ["od", "-tx1", str(hash_in)]), "61 62 63")
    assert_ok(run_bb(busybox, ["hexdump", "-C", str(hash_in)]), "61 62 63")
    assert_ok(run_bb(busybox, ["xxd", str(hash_in)]), "6162 63")
    strings_in = work / "strings.bin"
    strings_in.write_bytes(NUL_BYTE + b" printable_token " + NUL_BYTE)
    assert_ok(run_bb(busybox, ["strings", str(strings_in)]), "printable_token")
    assert_ok(
        run_bb(
            busybox,
            ["strings", "-f", "-o", "-t", "d"],
            stdin_data=f"abcd{TAB}efgh{NL}xy".encode(),
        ),
        "abcd",
    )
    assert_ok(run_bb(busybox, ["strings", "-t", "x", str(strings_in)]), "printable_token")
    assert_ok(run_bb(busybox, ["strings", str(strings_in), str(strings_in)]), "printable_token")
    assert run_bb(busybox, ["strings", str(work / "missing_str.bin")]).returncode != 0
    assert run_bb(busybox, ["strings", "-t", "z", str(strings_in)]).returncode != 0
    assert run_bb(busybox, ["strings", "-t", "ox", str(strings_in)]).returncode != 0
    assert run_bb(busybox, ["strings", "-t", "o_bad", str(strings_in)]).returncode != 0

    d2u = work / "d2u.txt"
    d2u.write_bytes(f"line1{CRLF}line2{CRLF}".encode())
    assert_ok(run_bb(busybox, ["dos2unix", str(d2u)]))
    assert d2u.read_bytes() == f"line1{NL}line2{NL}".encode()
    assert_ok(run_bb(busybox, ["unix2dos", str(d2u)]))
    assert d2u.read_bytes() == f"line1{CRLF}line2{CRLF}".encode()
    assert_ok(run_bb(busybox, ["dos2unix", "-d", str(d2u), str(d2u)]))
    assert_ok(run_bb(busybox, ["unix2dos", "-u", str(d2u)]))
    assert_ok(run_bb(busybox, ["dos2unix"], stdin_data=f"a{CRLF}".encode()), f"a{NL}")
    dangling_d2u = work / "dangling_d2u.txt"
    run_bb(busybox, ["ln", "-s", "missing_d2u_target", str(dangling_d2u)])
    assert run_bb(busybox, ["dos2unix", str(dangling_d2u)]).returncode != 0

    sub = work / "subdir"
    assert_ok(run_bb(busybox, ["mkdir", "-p", str(sub)]))
    touched = sub / "touched.txt"
    dot_touched = sub / ".dotitem"
    dotdot_touched = sub / "..dotdotitem"
    assert_ok(run_bb(busybox, ["touch", str(touched), str(dot_touched), str(dotdot_touched)]))
    assert touched.exists()
    assert_ok(
        run_bb(
            busybox,
            [
                "touch",
                "-c",
                str(touched),
                str(sub / "no_create_touch.txt"),
            ],
        )
    )
    assert run_bb(busybox, ["touch"]).returncode != 0
    assert run_bb(busybox, ["touch", str(sub / "no_parent_dir" / "bad.txt")]).returncode != 0
    assert run_bb(busybox, ["touch", "-c", "/dev/null/bad_touch"]).returncode != 0
    assert_ok(run_bb(busybox, ["chmod", "4755", str(touched)]))
    for mode_spec in (
        "644",
        "+X",
        "755",
        "+X",
        ",u+r,g-w",
        "ug=rx,+x,u+g-o=rwxXst",
        "=rx",
        "u=r,g=u,o=g",
        "u=rwx,g=u",
        "u+rx-w",
        "u+",
        "-x",
        "-",
    ):
        assert_ok(run_bb(busybox, ["chmod", "-v", "-c", mode_spec, str(touched)]))
    assert_ok(run_bb(busybox, ["chmod", "-c", "0755", str(touched), str(touched)]))
    assert_ok(run_bb(busybox, ["chmod", "--", "0755", str(touched)]))
    for bad_mode in ("0755z", "10000", "u", "u?", "invalid_mode"):
        assert run_bb(busybox, ["chmod", bad_mode, str(touched)]).returncode != 0
    assert run_bb(busybox, ["chmod"]).returncode != 0
    assert run_bb(busybox, ["chmod", "-v"]).returncode != 0
    assert run_bb(busybox, ["chmod", "-", "-"], cwd=sub).returncode != 0
    dangling_chmod = sub / "dangling_chmod"
    run_bb(busybox, ["ln", "-s", "missing_chmod_target", str(dangling_chmod)])
    assert run_bb(busybox, ["chmod", "-f", "644", str(dangling_chmod)]).returncode != 0
    assert run_bb(busybox, ["chmod", "644", str(sub / "no_chmod.txt")]).returncode != 0
    assert run_bb(busybox, ["chmod", "-f", "644", str(sub / "no_chmod.txt")]).returncode != 0
    assert run_bb(busybox, ["chmod", "644", "/dev/null"]).returncode != 0
    copied = sub / "copied.txt"
    assert_ok(run_bb(busybox, ["cp", str(hash_in), str(copied)]))
    assert run_bb(busybox, ["cp", str(copied / "not_a_dir"), str(sub / "bad_cp")]).returncode != 0
    cp_dir = sub / "cp_dir"
    cp_dir.mkdir()
    assert_ok(run_bb(busybox, ["cp", str(copied), str(cp_dir)]))
    assert_ok(run_bb(busybox, ["cp", str(copied), f"{cp_dir}/"]))
    assert_ok(run_bb(busybox, ["cp", "-L", "-P", str(copied), str(sub / "cp_lp.txt")]))
    assert_ok(run_bb(busybox, ["cp", "-t", str(cp_dir), str(copied), str(touched)]))
    assert_ok(run_bb(busybox, ["cp", str(copied), str(touched), str(cp_dir)]))
    assert_ok(run_bb(busybox, ["cp", "-T", str(copied), str(sub / "cp_T_file.txt")]))
    assert_ok(run_bb(busybox, ["cp", "-r", str(cp_dir), str(sub / "cp_r_new")]))
    assert_ok(run_bb(busybox, ["cp", "-r", str(cp_dir), str(sub / "cp_r_new")]))
    assert_ok(run_bb(busybox, ["cp", "-r", "-T", str(cp_dir), str(sub / "cp_r_T")]))
    assert_ok(run_bb(busybox, ["cp", "-r", str(copied), str(cp_dir)]))
    assert run_bb(busybox, ["cp", str(copied)]).returncode != 0
    assert run_bb(busybox, ["cp", "-T", "a", "b", "c"], cwd=sub).returncode != 0
    assert run_bb(busybox, ["cp", "-T", str(copied), str(cp_dir)]).returncode != 0
    assert run_bb(busybox, ["cp", str(copied), str(copied / "not_dir")]).returncode != 0
    assert run_bb(busybox, ["cp", str(sub / "missing_cp.txt"), str(cp_dir)]).returncode != 0
    moved = sub / "moved.txt"
    assert_ok(run_bb(busybox, ["mv", str(copied), str(moved)]))
    linked = sub / "hardlink.txt"
    assert_ok(run_bb(busybox, ["ln", str(moved), str(linked)]))
    sym_link = sub / "symlink.txt"
    run_bb(busybox, ["ln", "-s", str(moved), str(sym_link)])
    assert_ok(run_bb(busybox, ["chmod", "-R", "755", str(sub)]))
    assert run_bb(busybox, ["ln", "-T", "a", "b", "c"], cwd=sub).returncode != 0
    assert_ok(run_bb(busybox, ["ln", "-s", str(moved)], cwd=cp_dir))
    assert run_bb(busybox, ["ln", "-T", str(moved), str(cp_dir)]).returncode != 0
    assert_ok(run_bb(busybox, ["ln", "-s", "-f", "-v", str(moved), str(linked), str(cp_dir)]))
    assert run_bb(busybox, ["ln", str(sub / "no_such_src"), str(sub / "no_dst")]).returncode != 0
    dangling = sub / "dangling_sym"
    run_bb(busybox, ["ln", "-s", "missing_target_xyz", str(dangling)])
    run_bb(busybox, ["ln", str(dangling), str(sub / "dangling_hard")])
    run_bb(busybox, ["ln", "-s", "-n", "-b", str(moved), str(sym_link)])
    run_bb(busybox, ["ln", "-s", "-b", str(moved), str(sub / "brand_new_sym")])
    assert (
        run_bb(
            busybox,
            ["ln", "-s", "-b", "-S", "/no_such_dir/bak", str(moved), str(sym_link)],
        ).returncode
        != 0
    )
    assert run_bb(busybox, ["ln", "-s", str(moved), str(sym_link)]).returncode != 0
    assert run_bb(busybox, ["ln", "-s", "-f", str(moved), "/dev/null"]).returncode != 0
    (sub / "ctrl\x01\x7f.txt").write_text("c", encoding="utf-8")
    assert_ok(run_bb(busybox, ["ls", "-la", str(sub)]), "moved.txt")
    assert_ok(run_bb(busybox, ["stat", str(moved)]), "Size:")
    assert_ok(run_bb(busybox, ["stat", "-f", str(moved)]))
    assert_ok(run_bb(busybox, ["du", str(sub)]))
    assert_ok(run_bb(busybox, ["df", "."]))
    assert_ok(run_bb(busybox, ["df", "/"]))
    run_bb(busybox, ["df", "/dev"])
    assert run_bb(busybox, ["df", str(sub / "nonexistent_df_target")]).returncode != 0
    assert_ok(run_bb(busybox, ["id", "root"]))
    assert run_bb(busybox, ["id", "nonexistent_user_xyz_987"]).returncode != 0
    for printf_args in (
        ["printf", "%d\n", "42"],
        ["printf", "%d\n", "-42"],
        ["printf", "%u\n", "42"],
        ["printf", "%d\n", "'A"],
        ["printf", "%*d\n", "5", "42"],
    ):
        assert_ok(run_bb(busybox, printf_args))
    for bad_printf in (
        ["printf", "%u\n", "-1"],
        ["printf", "%u\n", "@bad"],
        ["printf", "%d\n", "-@bad"],
        ["printf", "%u\n", "999999999999999999999999999"],
        ["printf", "%d\n", "999999999999999999999999999"],
        ["printf", "%d\n", "-999999999999999999999999999"],
        ["printf", "%d\n", "12abc"],
        ["printf", "%d\n", "12!"],
        ["printf", "%*d\n", "5000000000", "1"],
        ["printf", "%*d\n", "99999999999", "1"],
        ["printf", "%*d\n", "-99999999999", "1"],
        ["printf", "%*d\n", "@bad", "1"],
        ["printf", "%*d\n", "-@bad", "1"],
    ):
        run_bb(busybox, bad_printf)
    for dd_args in (
        ["dd", "conv=notrunc", "if=/dev/null", "of=/dev/null"],
        ["dd", "status=none", "if=/dev/null", "of=/dev/null"],
    ):
        run_bb(busybox, dd_args)
    for bad_dd in (
        ["dd", "conv=not"],
        ["dd", "conv=xyz"],
        ["dd", "status=xyz"],
        ["dd", "badkey=1"],
    ):
        assert run_bb(busybox, bad_dd).returncode != 0
    assert_ok(run_bb(busybox, ["realpath", str(moved), str(linked)]))
    assert run_bb(busybox, ["realpath"]).returncode != 0
    assert (
        run_bb(busybox, ["realpath", str(sub / "missing_parent" / "nonexistent.txt")]).returncode
        != 0
    )
    run_bb(busybox, ["readlink", "-f", str(moved)])
    run_bb(busybox, ["readlink", "-n", str(sym_link)])
    assert run_bb(busybox, ["readlink", "-v", str(moved)]).returncode != 0

    trunc_file = sub / "trunc.bin"
    assert_ok(run_bb(busybox, ["truncate", "-s", "128", str(trunc_file)]))
    assert trunc_file.stat().st_size == 128
    assert_ok(run_bb(busybox, ["truncate", "-c", "-s", "10", str(sub / "no_create.bin")]))
    assert (
        run_bb(busybox, ["truncate", "-s", "10", str(sub / "no_parent" / "f.bin")]).returncode != 0
    )
    assert run_bb(busybox, ["truncate", "-c", "-s", "10", str(sub)]).returncode != 0
    assert (
        run_bb(busybox, ["truncate", "-s", "9223372036854775808", str(trunc_file)]).returncode != 0
    )
    assert_ok(run_bb(busybox, ["unlink", str(trunc_file)]))
    assert not trunc_file.exists()

    mktemp_out = assert_ok(run_bb(busybox, ["mktemp", "-p", str(sub), "tmp.XXXXXX"])).strip()
    assert Path(mktemp_out).exists()
    subprocess.run(
        _bb_cmd(busybox, ["mktemp", "-u"]),
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        check=True,
    )
    subprocess.run(
        _bb_cmd(busybox, ["mktemp", "-u", str(sub / "u.XXXXXX")]),
        env={"TMPDIR": ""},
        capture_output=True,
        check=True,
    )
    assert_ok(run_bb(busybox, ["mktemp", "-d", "-p", str(sub), "dir.XXXXXX"]))
    assert run_bb(busybox, ["mktemp", "-q", "-u", str(moved / "bad.XXXXXX")]).returncode != 0
    assert run_bb(busybox, ["mktemp", "-u", str(moved / "bad.XXXXXX")]).returncode != 0
    assert run_bb(busybox, ["mktemp", "-d", str(sub / "no_dir" / "bad.XXXXXX")]).returncode != 0
    assert run_bb(busybox, ["mktemp", str(sub / "no_dir" / "bad.XXXXXX")]).returncode != 0
    assert_ok(run_bb(busybox, ["rm", "-rf", str(sub), str(work / "nonexistent_rm_rf")]))
    assert not sub.exists()
    rmdir_target = work / "empty_dir"
    assert_ok(run_bb(busybox, ["mkdir", str(rmdir_target)]))
    assert_ok(run_bb(busybox, ["rmdir", str(rmdir_target)]))
    assert not rmdir_target.exists()
    assert_ok(run_bb(busybox, ["mkdir", "-v", "-m", "755", str(work / "m1"), str(work / "m2")]))
    assert run_bb(busybox, ["mkdir", "-m", "invalid_mode", str(work / "m_bad")]).returncode != 0
    assert run_bb(busybox, ["mkdir"]).returncode != 0
    assert run_bb(busybox, ["mkdir", str(work / "m1")]).returncode != 0
    (work / "m1" / "keep.txt").write_text("k", encoding="utf-8")
    assert_ok(
        run_bb(busybox, ["rm", "-i", str(work / "m1" / "keep.txt")], stdin_data=f"no{NL}".encode())
    )
    assert (work / "m1" / "keep.txt").exists()
    assert_ok(
        run_bb(
            busybox,
            ["rm", "-i", str(work / "m1" / "keep.txt")],
            stdin_data=f" {TAB}yes{NL}".encode(),
        )
    )
    (work / "m1" / "eof_y.txt").write_text("k", encoding="utf-8")
    assert_ok(run_bb(busybox, ["rm", "-i", str(work / "m1" / "eof_y.txt")], stdin_data=b"y"))
    (work / ".dotf").write_text("d", encoding="utf-8")
    (work / "..dotdotf").write_text("d", encoding="utf-8")
    assert_ok(run_bb(busybox, ["rm", "-v", str(work / ".dotf"), str(work / "..dotdotf")]))
    assert run_bb(busybox, ["rm", ".", ".."], cwd=work).returncode != 0
    assert run_bb(busybox, ["rm", str(work / "missing_rm.txt")]).returncode != 0
    assert_ok(run_bb(busybox, ["rm", "-f"]))
    assert run_bb(busybox, ["rm"]).returncode != 0
    assert run_bb(busybox, ["rmdir"]).returncode != 0
    (work / "m2" / "inside.txt").write_text("x", encoding="utf-8")
    assert_ok(run_bb(busybox, ["rmdir", "--ignore-fail-on-non-empty", str(work / "m2")]))
    assert (
        run_bb(busybox, ["rmdir", "--ignore-fail-on-non-empty", str(work / "no_rmdir")]).returncode
        != 0
    )
    assert run_bb(busybox, ["rmdir", str(work / "m2")]).returncode != 0
    assert_ok(run_bb(busybox, ["mkdir", "-p", "p_dir/c_dir", ".h_dir/c_dir"], cwd=work))
    assert_ok(run_bb(busybox, ["mkdir", "-p", "/", ".", "p_dir//c_dir"], cwd=work))
    (work / "file_blocking_dir").write_text("f", encoding="utf-8")
    assert run_bb(busybox, ["mkdir", "-p", "file_blocking_dir/sub"], cwd=work).returncode != 0
    assert_ok(run_bb(busybox, ["rmdir", "-v", "-p", "p_dir/c_dir", ".h_dir/c_dir"], cwd=work))
    rm_i_dir = work / "rm_i_dir"
    (rm_i_dir / "nested").mkdir(parents=True)
    (rm_i_dir / "nested" / "f.txt").write_text("x", encoding="utf-8")
    assert_ok(
        run_bb(busybox, ["rm", "-r", "-i", str(rm_i_dir)], stdin_data=f"n{NL}".encode()),
    )
    assert_ok(
        run_bb(
            busybox,
            ["rm", "-r", "-i", "-v", str(rm_i_dir)],
            stdin_data=f"y{NL}y{NL}n{NL}".encode(),
        ),
    )
    assert_ok(
        run_bb(
            busybox,
            ["rm", "-r", "-i", "-v", str(rm_i_dir)],
            stdin_data=f"y{NL}y{NL}y{NL}y{NL}".encode(),
        ),
    )
    assert run_bb(busybox, ["rm", str(work)]).returncode != 0
    assert run_bb(busybox, ["chmod", "-R", "755", str(work / "no_such_chmod_r")]).returncode != 0

    # Top-level busybox CLI, date, df, du, id, groups, mv, tr, cal, hexdump, xxd
    for bb_top in (
        [],
        ["--list"],
        ["--list-full"],
        ["--help"],
        ["--help", "echo"],
        ["true", "--help"],
    ):
        assert_ok(subprocess.run([str(busybox), *bb_top], capture_output=True, check=False))
    assert_ok(
        subprocess.run(
            ["-echo", "dash_applet"],
            executable=str(busybox),
            capture_output=True,
            check=False,
        ),
        "dash_applet",
    )
    assert (
        subprocess.run(
            [str(busybox), "nonexistent_applet_xyz"], capture_output=True, check=False
        ).returncode
        != 0
    )

    ref_f = work / "date_ref.txt"
    ref_f.write_text("r", encoding="utf-8")
    for date_args in (
        ["date"],
        ["date", "-u"],
        ["date", "-R"],
        ["date", "-I"],
        ["date", "-Idate"],
        ["date", "-Ihours"],
        ["date", "-Iminutes"],
        ["date", "-Iseconds"],
        ["date", "-Ins"],
        ["date", "-r", str(ref_f)],
        ["date", "+"],
        ["date", "+%Y"],
        ["date", "-d", "@0"],
        ["date", "-d", "12:34"],
        ["date", "-d", "12:34:56"],
        ["date", "-d", "2024-02-29 12:34:56"],
        ["date", "-d", "2024.02.29-12:34:56"],
        ["date", "-d", "02.29-12:34"],
        ["date", "-d", "30"],
        ["date", "-d", "1230"],
        ["date", "-d", "151230"],
        ["date", "-d", "02151230"],
        ["date", "-d", "2402151230"],
        ["date", "-d", "7002151230"],
        ["date", "-d", "202402151230.45"],
        ["date", "-D", "%Y-%m-%d", "-d", "2024-03-01"],
    ):
        assert_ok(run_bb(busybox, date_args))
    for bad_date in (
        ["date", "-Ibad"],
        ["date", "01020304"],
        ["date", "0102030424.15"],
        ["date", "010203042024"],
        ["date", "123"],
        ["date", "-d", "bad_date_str"],
        ["date", "-d", "123"],
        ["date", "-d", "202402151230.bad"],
        ["date", "-D", "%Y-%m-%d", "-d", "not_a_date"],
        ["date", "+%Y", "01020304", "extra"],
    ):
        assert run_bb(busybox, bad_date).returncode != 0

    for df_args in (
        ["df"],
        ["df", "-k"],
        ["df", "-m"],
        ["df", "-h"],
        ["df", "-H"],
        ["df", "-i"],
        ["df", "-i", "-h"],
        ["df", "-P"],
        ["df", "-T"],
        ["df", "-a"],
        ["df", "-B", "KiB", "."],
        ["df", "-B", "4096", "."],
        ["df", "-t", "apfs", "-t", "ext4"],
        ["df", "."],
    ):
        run_bb(busybox, df_args, cwd=work)
    subprocess.run(
        _bb_cmd(busybox, ["df", "."]),
        env={**os.environ, "POSIXLY_CORRECT": "1"},
        capture_output=True,
        check=False,
    )
    assert run_bb(busybox, ["df", str(work / "nonexistent_mount_xyz")]).returncode != 0

    du_dir = work / "du_dir"
    (du_dir / "sub").mkdir(parents=True)
    du_f1 = du_dir / "f1.txt"
    du_f1.write_bytes(b"x" * 2048)
    os.link(du_f1, du_dir / "f1_hard.txt")
    (du_dir / "sym_dir").symlink_to(du_dir / "sub")
    (du_dir / "broken_sym").symlink_to(du_dir / "missing_target")
    for du_args in (
        ["du"],
        ["du", "-H"],
        ["du", "-a", "-b", "-c", "-x", str(du_dir)],
        ["du", "-h", "-l", "-d", "1", str(du_dir)],
        ["du", "-m", "-s", str(du_dir)],
        ["du", "-k", "-H", str(du_dir / "sym_dir")],
        ["du", "-L", str(du_dir)],
    ):
        run_bb(busybox, du_args, cwd=du_dir)
    subprocess.run(
        _bb_cmd(busybox, ["du", str(du_dir)]),
        env={**os.environ, "POSIXLY_CORRECT": "1"},
        capture_output=True,
        check=False,
    )
    assert run_bb(busybox, ["du", str(du_dir / "missing_du")]).returncode != 0

    for id_args in (
        ["id"],
        ["id", "-u"],
        ["id", "-g"],
        ["id", "-G"],
        ["id", "-un"],
        ["id", "-gn"],
        ["id", "-Gn"],
        ["id", "-ur"],
        ["id", "-gr"],
        ["id", "-Gr"],
        ["id", "root"],
        ["id", "-G", "root"],
    ):
        assert_ok(run_bb(busybox, id_args))
    assert run_bb(busybox, ["id", "nonexistent_user_xyz_999"]).returncode != 0

    mv_dir = work / "mv_dir"
    mv_dir.mkdir()
    mv_src = work / "mv_s.txt"
    mv_dst = work / "mv_d.txt"
    mv_src.write_text("s", encoding="utf-8")
    mv_dst.write_text("d", encoding="utf-8")
    assert_ok(run_bb(busybox, ["mv", "-n", "-v", str(mv_src), str(mv_dst)]))
    assert mv_src.exists()
    assert_ok(run_bb(busybox, ["mv", "-i", str(mv_src), str(mv_dst)], stdin_data=f"n{NL}".encode()))
    assert mv_src.exists()
    assert_ok(run_bb(busybox, ["mv", "-i", str(mv_src), str(mv_dst)], stdin_data=f"y{NL}".encode()))
    mv_src.write_text("s2", encoding="utf-8")
    assert_ok(run_bb(busybox, ["mv", str(mv_src), str(mv_dst)]))
    assert_ok(run_bb(busybox, ["mv", "-v", "-t", str(mv_dir), str(mv_dst)]))
    mv_d2 = work / "mv_dir2"
    mv_d2.mkdir()
    assert_ok(run_bb(busybox, ["mv", "-T", str(mv_d2), str(work / "mv_dir2_renamed")]))
    mv_m1 = work / "mv_m1.txt"
    mv_m2 = work / "mv_m2.txt"
    mv_m1.write_text("1", encoding="utf-8")
    mv_m2.write_text("2", encoding="utf-8")
    assert_ok(run_bb(busybox, ["mv", str(mv_m1), str(mv_m2), str(mv_dir)]))
    (work / "mv_file_t.txt").write_text("t", encoding="utf-8")
    for bad_mv in (
        ["mv", str(work / "mv_file_t.txt")],
        ["mv", "-T", str(work / "mv_file_t.txt"), str(mv_dir), "extra"],
        ["mv", "-T", str(work / "mv_file_t.txt"), str(mv_dir)],
        ["mv", str(work / "missing_mv_src.txt"), str(mv_dir)],
    ):
        assert run_bb(busybox, bad_mv).returncode != 0

    for tr_args, tr_in in (
        (["tr", "a-z0-", "A-Z_"], b"abc0-" * 200),
        (["tr", r"\-a", "XY"], b"-a"),
        (["tr", r"\041-\043", "123"], b'!"#'),
        (
            [
                "tr",
                "-d",
                "[:alpha:][:alnum:][:digit:][:lower:][:upper:][:space:]"
                "[:blank:][:punct:][:cntrl:][:xdigit:][:bad:][=a=][x]",
            ],
            b"abc123 \t\n!@#\x01",
        ),
        (["tr", "-c", "-s", "a", "b"], b"aaaxxxyaaa"),
        (["tr", "-s", "a", "b"], b"aaabbbaaa"),
        (["tr", "-d", "a"], b"aaa"),
    ):
        assert_ok(run_bb(busybox, tr_args, stdin_data=tr_in))
    assert run_bb(busybox, ["tr", "a", ""], stdin_data=b"a").returncode != 0
    assert_ok(
        run_bb(
            busybox,
            ["base64", "-d"],
            stdin_data=(("09AZaz+/YQ==YQo=!{" + "YWJj" * 25) + NL).encode("ascii"),
        )
    )
    assert run_bb(busybox, ["base64", "-d"], stdin_data=b"YQ\n").returncode != 0
    assert_ok(
        run_bb(
            busybox,
            ["base32", "-d"],
            stdin_data=f"MFRGGZDF27======!{NL}".encode("ascii"),
        )
    )
    run_bb(busybox, ["test", "-r", "/etc/passwd"])

    for cal_args in (
        ["cal"],
        ["cal", "-y"],
        ["cal", "-j"],
        ["cal", "-m", "-y", "2", "2024"],
        ["cal", "2", "2024"],
        ["cal", "2", "1900"],
        ["cal", "2", "2000"],
        ["cal", "2", "1700"],
        ["cal", "2", "1600"],
        ["cal", "9", "1752"],
        ["cal", "-j", "9", "1752"],
        ["cal", "-j", "2024"],
        ["cal", "2024"],
    ):
        assert_ok(run_bb(busybox, cal_args))
    for bad_cal in (["cal", "13", "2024"], ["cal", "1", "10000"], ["cal", "1", "2", "3"]):
        assert run_bb(busybox, bad_cal).returncode != 0

    hd_in = work / "1hd_in.bin"
    hd_in.write_bytes(bytes(range(64)) + b"\x00" * 64)
    hd_fmt = work / "hd_fmt.txt"
    hd_fmt.write_text(f'# comment{NL}{NL}4/1 "%02x " "\\n"{NL}', encoding="utf-8")
    for hd_args in (
        ["hexdump", str(hd_in)],
        ["hexdump", "-b", "-c", "-C", "-d", "-o", "-x", "-v", "-n", "32", "-s", "4", str(hd_in)],
        ["hexdump", "-e", '4/1 "%02x " "\\n"', "-f", str(hd_fmt), str(hd_in)],
    ):
        assert_ok(run_bb(busybox, hd_args))
    assert run_bb(busybox, ["hexdump", "-z"]).returncode != 0
    assert run_bb(busybox, ["hexdump", "-f", str(work / "no_fmt.txt")]).returncode != 0

    for xxd_args in (
        ["xxd", "-a", "-l", "64", "-s", "4", "-o", "16", str(hd_in)],
        ["xxd", "-p", "-c", "8", str(hd_in)],
        ["xxd", "-i", "-g", "1", "1hd_in.bin"],
        ["xxd", "-g", "0", str(hd_in)],
        ["xxd", "-g", "2", str(hd_in)],
        ["xxd", "-g", "3", str(hd_in)],
        ["xxd", "-g", "4", str(hd_in)],
    ):
        assert_ok(run_bb(busybox, xxd_args, cwd=work))
    xxd_hex = assert_ok(run_bb(busybox, ["xxd", str(hd_in)])).encode()
    assert_ok(run_bb(busybox, ["xxd", "-r"], stdin_data=xxd_hex))
    assert_ok(
        run_bb(
            busybox,
            ["xxd", "-r", "-p"],
            stdin_data=f"4142 430a 4z 4{NL}".encode("ascii"),
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["xxd", "-r"],
            stdin_data=(
                f"00000000: 4142 4344  ABCD{NL}"
                f"00000010: 4546 47    EFG{NL}"
                f"00000014: 3f3f3f     ???{NL}"
                f"00000018: 4z 4       bad{NL}"
            ).encode("ascii"),
        )
    )
    for bad_xxd in (
        ["xxd", str(hd_in), "extra_arg"],
        ["xxd", "-r", str(work / "nonexistent_xxd.txt")],
    ):
        assert run_bb(busybox, bad_xxd).returncode != 0

    _exercise_batch_coreutils_applets(busybox, work)


def _exercise_batch_coreutils_applets(busybox: Path, work: Path) -> None:
    """Exercise ``head``, ``cut``, ``printf``, ``stat``, ``tail``, and ``test`` branches.

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        work: Isolated temporary directory for test fixtures.
    """
    bdir = work / "batch_cu"
    bdir.mkdir(parents=True, exist_ok=True)
    f_five = bdir / "five.txt"
    f_five.write_text(f"l1{NL}l2{NL}l3{NL}l4{NL}l5{NL}", encoding="utf-8")
    f_two = bdir / "two.txt"
    f_two.write_text(f"a{NL}b{NL}", encoding="utf-8")
    f_empty = bdir / "empty.txt"
    f_empty.write_bytes(b"")

    # head: legacy -N, -q, -v, -c, -n, negative N bytes/lines, stdin & multiple files
    for head_args, head_in in (
        (["head", "-2", str(f_five)], None),
        (["head", "2", str(f_five)], None),
        (["head", "-a", str(f_five)], None),
        (["head", "-q", "-v", "-c", "3", str(f_five), str(f_two)], None),
        (["head", "-q", "-n", "2", str(f_five), str(f_two)], None),
        (["head", "-v", "-n", "1"], f"s1{NL}s2{NL}".encode()),
        (["head", "-c", "-2"], b"abcdef"),
        (["head", "-c", "-10"], b"ab"),
        (["head", "-n", "-2"], f"1{NL}2{NL}3{NL}4{NL}5{NL}".encode()),
        (["head", "-n", "-5"], f"1{NL}2{NL}".encode()),
        (["head", str(f_two)], None),
        (["head"], f"stdin_default{NL}".encode()),
    ):
        if len(head_args) > 1 and head_args[1] in ("-a", "2"):
            assert run_bb(busybox, head_args, stdin_data=head_in).returncode != 0
        else:
            assert_ok(run_bb(busybox, head_args, stdin_data=head_in))
    assert run_bb(busybox, ["head", "-z"]).returncode != 0
    assert run_bb(busybox, ["head", str(bdir / "missing_head.txt")]).returncode != 0

    # cut: -b/-c overlapping & odelim, -d '\n' line cutting, -f, -F regex, -D, -s
    for cut_args, cut_in in (
        (["cut", "-b", "1-2,2-3,5-6", "-O", ":", str(f_five), str(f_two)], None),
        (["cut", "-b", "1-2,1-3", "-O", ":"], f"abcdef{NL}".encode()),
        (["cut", "-b", "1,3-4", "-O", ":"], f"abcdef{NL}".encode()),
        (["cut", "-D", "-b", "2-3,1-2", "-O", ":"], f"abcdef{NL}".encode()),
        (["cut", "-b", "1-2,2-3"], f"abcdef{NL}".encode()),
        (["cut", "-c", "-2,4-", "-O", ""], f"abcdef{NL}".encode()),
        (["cut", "-b", "2-3", "--output-delimiter=:"], f"abcdef{NL}".encode()),
        (
            ["cut", "-d", NL, "-f", "2,4-5", "-O", "^"],
            f"l1{NL}l2{NL}l3{NL}l4{NL}l5{NL}l6{NL}".encode(),
        ),
        (
            ["cut", "-D", "-d", NL, "-f", "4-5,1-2", "-O", "^"],
            f"l1{NL}l2{NL}l3{NL}l4{NL}l5{NL}".encode(),
        ),
        (["cut", "-d", NL, "-f", "5"], f"l1{NL}l2{NL}".encode()),
        (["cut", "-d:", "-f", "1,3", "-s"], f"{NL}no_delim{NL}a:b{NL}a:b:c:d{NL}".encode()),
        (["cut", "-d:", "-O", "", "-f", "1,2"], f"a:b{NL}".encode()),
        (["cut", "-d:", "-f", "2"], f"{NL}no_delim{NL}a:b:c{NL}".encode()),
        (["cut", "-D", "-d:", "-f", "3,1", "-s"], f"no_delim{NL}a:b:c{NL}x:y{NL}".encode()),
        (["cut", "-F", "2,4-5,6", "-O", ":"], f"1 2  3  4  5  6  7{NL}".encode()),
        (["cut", "-d", "@+", "-F", "1-"], f"no at sign{NL}a@@b{NL}@a{NL}".encode()),
        (["cut", "-d", "@+", "-F", "2"], f"@a{NL}".encode()),
    ):
        assert_ok(run_bb(busybox, cut_args, stdin_data=cut_in))
    for bad_cut in (
        ["cut", "-b", "1", "-s"],
        ["cut", "-b", "1", "-d:"],
        ["cut", "-f", ""],
        ["cut", "-f", "-"],
        ["cut", "-f", "3-1"],
        ["cut", "-f", "1", str(bdir / "missing_cut.txt")],
    ):
        assert run_bb(busybox, bad_cut).returncode != 0

    # printf: %b \0ooo & \c, \c in format, %c/%s/%d/%u/%x/%f, *.* width/prec, modifiers
    for pf_args in (
        ["printf", r"%% %b\n", r"\"" + r"\0101\08\n"],
        ["printf", "%d %d\n", '"A', "'B"],
        ["printf", "%b", r"stop\cmore"],
        ["printf", r"fmt\cafter\n"],
        ["printf", "%c %s %*s %.*s %*.*s\n", "Z", "hi", "4", "a", "2", "abcd", "4", "2", "abcd"],
        [
            "printf",
            "%-5.2d %hd %.*d %*.*d %+lld %Lu %zx\n",
            "3",
            "+7",
            "3",
            "5",
            "5",
            "3",
            "5",
            "9",
            "+10",
            "-2",
        ],
        ["printf", "%f %*f %.*f %*.*f\n", "1.5", "6", "1.5", "2", "1.5", "6", "2", "1.5"],
        ["printf", "%b %s %*.*s\n"],
        ["printf", "%s\n", "one", "two"],
    ):
        assert_ok(run_bb(busybox, pf_args))
    run_bb(busybox, ["printf", "%d %s %*.*f\n"])
    run_bb(busybox, ["printf", "%.*d\n", "bad_prec", "1"])
    for bad_pf in (
        ["printf"],
        ["printf", "%f\n", "1.5bad"],
        ["printf", "%"],
        ["printf", "--", "%-"],
        ["printf", "%q", "1"],
    ):
        assert run_bb(busybox, bad_pf).returncode != 0
    subprocess.run(
        _bb_cmd(busybox, ["printf", "closed_out\n"]),
        preexec_fn=lambda: os.close(1),
        stderr=subprocess.PIPE,
        check=False,
    )

    # stat: all file types (empty, regular, dir, symlink, fifo, sock, chr/blk), formats, -f, -t, -L
    st_sym = bdir / "sym.txt"
    st_sym.symlink_to(f_five)
    st_bad_sym = bdir / "bad_sym.txt"
    st_bad_sym.symlink_to(bdir / "nonexistent_target")
    st_fifo = bdir / "fifo.pipe"
    os.mkfifo(str(st_fifo))
    with tempfile.TemporaryDirectory(prefix="bb_st_") as sock_dir:
        st_sock_path = Path(sock_dir) / "s"
        st_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        st_sock.bind(str(st_sock_path))
        try:
            all_file_fmt = (
                "%n %N %d %D %i %a %A %f %F %h %u %U %g %G "
                "%t %T %s %B %b %o %x %X %y %Y %z %Z %Q %% %"
            )
            all_fs_fmt = "%n %i %l %t %T %b %f %a %s %S %c %d %Q %% %"
            for st_target in (
                f_empty,
                f_five,
                bdir,
                st_sym,
                st_fifo,
                st_sock_path,
                Path("/dev/null"),
            ):
                assert_ok(run_bb(busybox, ["stat", str(st_target)]))
                assert_ok(run_bb(busybox, ["stat", "-t", str(st_target)]))
                assert_ok(run_bb(busybox, ["stat", "-c", all_file_fmt, str(st_target)]))
            for blk_candidate in sorted(Path("/dev").glob("disk*"))[:1]:
                run_bb(busybox, ["stat", str(blk_candidate)])
            assert_ok(run_bb(busybox, ["stat", "-L", str(st_sym)]))
            assert_ok(run_bb(busybox, ["stat", "-f", str(f_five)]))
            assert_ok(run_bb(busybox, ["stat", "-f", "-t", str(f_five)]))
            assert_ok(run_bb(busybox, ["stat", "-f", "-c", all_fs_fmt, str(f_five)]))
            assert run_bb(busybox, ["stat", "-L", str(st_bad_sym)]).returncode != 0
            assert run_bb(busybox, ["stat", "-f", str(bdir / "no_such_fs")]).returncode != 0
        finally:
            st_sock.close()
            st_sock_path.unlink(missing_ok=True)

    # tail: +N/-N, -c/-n, -q/-v, seekable & non-seekable (stdin), large buffers, -f/-F
    big_tail = bdir / "big_tail.txt"
    big_tail.write_bytes((b"0123456789abcdef" * 600 + b"\n") * 5 + b"last_no_nl")
    huge_tail = bdir / "huge_tail.txt"
    huge_tail.write_bytes(b"a" * (1100 * 1024) + b"\nlast_huge\n")
    for tail_args, tail_in in (
        (["tail", "-2", str(f_five)], None),
        (["tail", "-1", str(huge_tail)], None),
        (["tail", "+2", str(f_five)], None),
        (["tail", "2", str(f_five)], None),
        (["tail", "-a", str(f_five)], None),
        (["tail", "-q", "-v", "-n", "2", str(f_five), str(f_two)], None),
        (["tail", "-v", "-q", "-n", "2", str(f_five), str(f_two)], None),
        (["tail", "-c", "0", str(f_five)], None),
        (["tail", "-c", "4", str(f_five)], None),
        (["tail", "-c", "9999", str(f_five)], None),
        (["tail", "-n", "999999", str(f_five)], None),
        (["tail", "-c", "+3"], b"abcdef"),
        (["tail", "-c", "+20"], b"abcdef"),
        (["tail", "-n", "+2"], f"l1{NL}l2{NL}l3{NL}".encode()),
        (["tail", "-n", "+1"], f"l1{NL}l2{NL}".encode()),
        (["tail", "-n", "+10"], f"l1{NL}l2{NL}".encode()),
        (["tail", "-n", "0"], f"l1{NL}l2{NL}".encode()),
        (["tail", "-c", "5"], b"0123456789abcdef"),
        (["tail", "-c", "70000"], b"x" * 75000),
        (["tail", "-n", "3"], big_tail.read_bytes()),
        (["tail", "-n", "5"], (b"0123456789abcdef\n" * 65 + b"short\n")),
        (["tail", "-n", "10"], f"l1{NL}l2{NL}".encode()),
        (["tail", "-n", "2"], f"l1{NL}l2{NL}l3{NL}".encode()),
        (["tail", "-f"], b"pipe_stdin_stops_follow\n"),
        (["tail"], f"default_stdin{NL}".encode()),
    ):
        if len(tail_args) > 1 and tail_args[1] in ("-a", "2"):
            assert run_bb(busybox, tail_args, stdin_data=tail_in).returncode != 0
        else:
            assert_ok(run_bb(busybox, tail_args, stdin_data=tail_in))
    with f_five.open("rb") as reg_stdin:
        subprocess.run(
            _bb_cmd(busybox, ["tail"]),
            stdin=reg_stdin,
            capture_output=True,
            check=True,
        )
    r_pipe, w_pipe = os.pipe()
    tail_stream = subprocess.Popen(
        _bb_cmd(busybox, ["tail", "-n", "5"]),
        stdin=r_pipe,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(r_pipe)
    os.write(w_pipe, b"line1\nline2\n")
    time.sleep(0.1)
    os.write(w_pipe, b"line3\nline4\nline5\nline6\n")
    os.close(w_pipe)
    tail_stream.communicate()
    wo_tail_fd = os.open(str(f_five), os.O_WRONLY)
    try:
        subprocess.run(
            _bb_cmd(busybox, ["tail", "-n", "1"]),
            stdin=wo_tail_fd,
            capture_output=True,
            check=False,
        )
    finally:
        os.close(wo_tail_fd)
    assert run_bb(busybox, ["tail", str(bdir / "no_such_tail.txt")]).returncode != 0
    assert run_bb(busybox, ["tail", str(bdir / "no_such_1.txt"), str(f_two)]).returncode != 0

    for follow_flag in ("-F", "-f"):
        f_follow1 = bdir / f"follow1_{follow_flag}.txt"
        f_follow1.write_text(f"init1{NL}", encoding="utf-8")
        f_follow2 = bdir / f"follow2_{follow_flag}.txt"
        f_follow3 = bdir / f"follow3_{follow_flag}.txt"
        if follow_flag == "-f":
            f_follow2.write_text(f"init2{NL}", encoding="utf-8")
        tail_proc = subprocess.Popen(
            _bb_cmd(
                busybox,
                ["tail", follow_flag, "-s", "1"]
                + (["-q"] if follow_flag == "-f" else ["-v"])
                + [str(f_follow1), str(f_follow2)]
                + ([str(f_follow3)] if follow_flag == "-F" else []),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.3)
            f_follow2.write_text(f"appeared2{NL}", encoding="utf-8")
            with f_follow1.open("a", encoding="utf-8") as f1_app:
                f1_app.write(f"appended_long_line{NL}")
            time.sleep(1.2)
            f_follow1.write_text(f"s{NL}", encoding="utf-8")
            if follow_flag == "-F":
                f_follow1.unlink()
                f_follow1.write_text(f"replaced1{NL}", encoding="utf-8")
                f_follow2.unlink()
            time.sleep(1.2)
        finally:
            tail_proc.terminate()
            tail_proc.wait()

    # test / [ / [[: unary, binary, logical, parens, bash [[ =~, &&, ||, fnmatch ]], errors
    f_older = bdir / "older.txt"
    f_newer = bdir / "newer.txt"
    f_older.write_text("old", encoding="utf-8")
    f_newer.write_text("new", encoding="utf-8")
    os.utime(str(f_older), (1000, 1000))
    os.utime(str(f_newer), (2000, 2000))
    f_mode_allx = bdir / "allx.txt"
    f_mode_allx.write_text("x", encoding="utf-8")
    f_mode_allx.chmod(0o755)
    f_mode_ux = bdir / "ux.txt"
    f_mode_ux.write_text("x", encoding="utf-8")
    f_mode_ux.chmod(0o700)
    f_mode_nox = bdir / "nox.txt"
    f_mode_nox.write_text("x", encoding="utf-8")
    f_mode_nox.chmod(0o600)

    for test_cmd in (
        ["test"],
        ["test", ""],
        ["test", "nonempty"],
        ["test", "!", ""],
        ["test", "!", "nonempty"],
        ["[", "]"],
        ["[", "!", "]"],
        ["[", "a", "-a", "!", "]"],
        ["[[", "]]"],
        ["test", "(", "nonempty", ")"],
        ["test", "!", "(", "nonempty", ")"],
        ["test", "(", "-f", str(f_five), ")"],
        ["test", "(", "!", "-e", "nonexistent_xyz", ")"],
        ["test", "(", "a", "-a", "b"],
        ["test", "!", "a", "-a", "b"],
        ["test", "(", "a", "-a", "b", "c"],
        ["test", "a", "-a", "b", "-a", "-f"],
        ["test", "a", "-a", "b", "-a", ""],
        ["test", "(", "-f", str(f_five), "-a", "-d", str(bdir), ")"],
        ["test", "-r", str(f_five), "-o", "-w", str(f_five)],
        ["test", "-x", str(f_mode_allx)],
        ["test", "-x", str(f_mode_ux)],
        ["test", "-x", str(f_mode_nox)],
        ["test", "-r", "/etc/passwd"],
        ["test", "-w", "/etc/passwd"],
        ["test", "-r", "/dev/null"],
        ["test", "-r", "/var/root"],
        ["test", "-w", "/"],
        ["test", "-e", str(f_five)],
        ["test", "-e", str(bdir / "missing_xyz")],
        ["test", "-f", str(f_five)],
        ["test", "-d", str(bdir)],
        ["test", "-c", "/dev/null"],
        ["test", "-b", str(f_five)],
        ["test", "-p", str(st_fifo)],
        ["test", "-S", str(f_five)],
        ["test", "-L", str(st_sym)],
        ["test", "-h", str(bdir / "missing_sym")],
        ["test", "-u", str(f_five)],
        ["test", "-g", str(f_five)],
        ["test", "-k", str(f_five)],
        ["test", "-s", str(f_five)],
        ["test", "-O", str(f_five)],
        ["test", "-G", str(f_five)],
        ["test", "-z", ""],
        ["test", "-n", "abc"],
        ["test", "-t", "0"],
        ["test", "-f"],
        ["test", "a", "=", "a"],
        ["test", "a", "==", "b"],
        ["test", "a", "!=", "b"],
        ["test", "a", "<", "b"],
        ["test", "b", ">", "a"],
        ["test", "1", "-eq", "1"],
        ["test", "1", "-ne", "2"],
        ["test", "2", "-ge", "1"],
        ["test", "2", "-gt", "1"],
        ["test", "1", "-le", "2"],
        ["test", "1", "-lt", "2"],
        ["test", str(f_newer), "-nt", str(f_older)],
        ["test", str(f_older), "-ot", str(f_newer)],
        ["test", str(f_five), "-ef", str(f_five)],
        ["test", str(f_five), "-ef", str(f_two)],
        ["test", str(f_five), "-nt", str(bdir / "missing_nt")],
        ["test", str(bdir / "missing_nt"), "-nt", str(f_five)],
        ["test", "1", "-eq", "1", "-a", "2", "-eq", "2", "-o", "3", "-eq", "4"],
        ["test", "1", "-eq", "2", "-a", "2", "-eq", "2", "-o", "3", "-eq", "4"],
        ["test", "a", "-a", "b", "=", "b"],
        ["test", "a", "-a", "=", "b"],
        ["test", "!", "a", "-a", "b"],
        ["test", "a", "=~", "a"],
        ["test", "&&"],
        ["[[", "hello", "==", "h*o", "]]"],
        ["[[", "hello", "!=", "x*", "]]"],
        ["[[", "hello123", "=~", "^hello[0-9]+$", "]]"],
        ["[[", "a", "<", "b", "]]"],
        ["[[", "a", "==", "a", "&&", "b", "==", "b", "||", "c", "==", "d", "]]"],
        ["[[", "-a", "==", "-a", "]]"],
    ):
        run_bb(busybox, test_cmd)
    for err_test in (
        ["[", "a"],
        ["[[", "a"],
        ["[[", "a", "=~", "[", "]]"],
        ["[[", "a", "-a", "b", "]]"],
        ["test", "1", "-eq", "99999999999999999999999999999"],
        ["test", "1", "-eq", "bad_num"],
        ["test", "1", "-eq", "12bad"],
        ["test", "(", "a"],
        ["test", "(", ")"],
        ["test", "a", "-a"],
        ["test", "1", "-lt", "2", "extra_op"],
    ):
        assert run_bb(busybox, err_test).returncode == 2

    # cp & copy_file: symlinks, hardlinks, interactive, force, remove-destination, recursion, etc.
    cp_bdir = bdir / "cp_ext"
    cp_bdir.mkdir(parents=True, exist_ok=True)
    cp_src = cp_bdir / "src.txt"
    cp_src.write_text("cp_src_content", encoding="utf-8")
    cp_src_hard = cp_bdir / "src_hard.txt"
    os.link(cp_src, cp_src_hard)
    cp_dst = cp_bdir / "dst.txt"
    cp_dst.write_text("cp_dst_old", encoding="utf-8")
    assert run_bb(busybox, ["cp", str(cp_src), str(cp_src)]).returncode != 0
    assert run_bb(busybox, ["cp", str(cp_src), str(cp_src_hard)]).returncode != 0
    assert_ok(run_bb(busybox, ["cp", "-n", "-v", str(cp_src), str(cp_dst)]))
    assert cp_dst.read_text(encoding="utf-8") == "cp_dst_old"
    os.utime(cp_src, (1000, 1000))
    os.utime(cp_dst, (2000, 2000))
    assert_ok(run_bb(busybox, ["cp", "-u", str(cp_src), str(cp_dst)]))
    os.utime(cp_src, (3000, 3000))
    assert_ok(run_bb(busybox, ["cp", "-u", "-v", str(cp_src), str(cp_dst)]))
    assert_ok(
        run_bb(
            busybox,
            ["cp", "-i", str(cp_src), str(cp_dst)],
            stdin_data=f"n{NL}".encode(),
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["cp", "-i", "-p", "-v", str(cp_src), str(cp_dst)],
            stdin_data=f"y{NL}".encode(),
        )
    )
    assert_ok(run_bb(busybox, ["cp", "-s", str(cp_bdir / "missing_sym_src"), str(cp_bdir / "s1")]))
    assert run_bb(busybox, ["cp", "-s", str(cp_src), str(cp_bdir / "s1")]).returncode != 0
    assert_ok(
        run_bb(
            busybox,
            ["cp", "-s", "-i", str(cp_src), str(cp_bdir / "s1")],
            stdin_data=f"n{NL}".encode(),
        )
    )
    assert_ok(run_bb(busybox, ["cp", "-s", "-f", str(cp_src), str(cp_bdir / "s1")]))
    assert (
        run_bb(busybox, ["cp", "-s", "-f", str(cp_src), str(cp_bdir / "no_dir" / "s")]).returncode
        != 0
    )
    assert run_bb(busybox, ["cp", "-l", str(cp_src), "/dev/null"]).returncode != 0
    assert_ok(run_bb(busybox, ["cp", "-l", str(cp_src), str(cp_bdir / "l1")]))
    (cp_bdir / "l2").write_text("other_file", encoding="utf-8")
    assert_ok(run_bb(busybox, ["cp", "-l", "-f", str(cp_src), str(cp_bdir / "l2")]))
    assert run_bb(busybox, ["cp", str(cp_bdir), str(cp_bdir / "sub_err")]).returncode != 0
    assert run_bb(busybox, ["cp", "-r", str(cp_bdir), str(cp_bdir / "rec_sub")]).returncode != 0
    assert run_bb(busybox, ["cp", "-r", str(cp_bdir), str(cp_dst)]).returncode != 0
    assert run_bb(busybox, ["cp", "-T", "-r", str(cp_bdir), str(cp_dst)]).returncode != 0
    assert run_bb(busybox, ["ls", str(bdir / "no_such_ls_dir_xyz")]).returncode != 0
    assert run_bb(busybox, ["date", "-r", str(bdir / "no_such_date_ref_xyz")]).returncode != 0
    assert (
        run_bb(busybox, ["cp", "-r", str(cp_bdir), str(cp_bdir / "no_p" / "sub" / "d")]).returncode
        != 0
    )
    cp_tree = cp_bdir / "tree"
    cp_tree.mkdir(parents=True, exist_ok=True)
    t_f1 = cp_tree / "f1.txt"
    t_f1.write_text("hard", encoding="utf-8")
    os.link(t_f1, cp_tree / "f2.txt")
    (cp_tree / "sym_rel").symlink_to("f1.txt")
    os.mkfifo(str(cp_tree / "fifo_node"))
    unreadable_f = cp_tree / "unreadable.txt"
    unreadable_f.write_text("sec", encoding="utf-8")
    unreadable_f.chmod(0)
    cp_tree_dst = cp_bdir / "tree_dst"
    cp_tree_dst.mkdir(parents=True, exist_ok=True)
    (cp_tree_dst / "f2.txt").write_text("occupied", encoding="utf-8")
    (cp_tree_dst / "sym_rel").write_text("occupied_sym", encoding="utf-8")
    unreadable_dir = cp_bdir / "unreadable_dir"
    unreadable_dir.mkdir(parents=True, exist_ok=True)
    unreadable_dir.chmod(0)
    try:
        run_bb(
            busybox,
            ["du", str(unreadable_dir)],
        )
        run_bb(
            busybox,
            ["cp", "-T", "-r", str(cp_tree), str(cp_tree_dst)],
        )
        run_bb(
            busybox,
            ["cp", "-a", "-i", str(cp_tree), str(cp_bdir / "tree_dst_i")],
        )
        run_bb(
            busybox,
            ["cp", "-r", "-P", str(cp_tree / "sym_rel"), str(cp_tree_dst / "sym_rel")],
        )
        run_bb(
            busybox,
            ["cp", "-P", str(cp_tree / "sym_rel"), str(cp_tree_dst / "sym_single")],
        )
        run_bb(
            busybox,
            ["cp", "-a", "-f", "-v", str(cp_tree / "sym_rel"), str(cp_tree_dst / "sym_rel")],
        )
        run_bb(
            busybox,
            ["cp", "-r", "-P", str(cp_tree / "fifo_node"), str(cp_tree_dst / "fifo_copy")],
        )
        run_bb(
            busybox,
            ["cp", "/dev/null", str(cp_tree_dst / "devnull_copy")],
        )
        run_bb(
            busybox,
            ["cp", "-a", str(t_f1), str(cp_tree / "f2.txt"), str(cp_tree_dst)],
        )
        run_bb(
            busybox,
            ["cp", "-a", "-f", str(t_f1), str(cp_tree / "f2.txt"), str(cp_tree_dst)],
        )
        run_bb(busybox, ["cp", "-r", str(cp_tree), str(cp_bdir / "tree_err_copy")])
    finally:
        unreadable_f.chmod(0o644)
        unreadable_dir.chmod(0o755)

    # hexdump & dump.c: all conversion specifiers, byte counts, precisions, skip, dup, errors
    dump_bin = bdir / "dump_all.bin"
    dump_bin.write_bytes(
        bytes([0x00, 0x07, 0x7F, 0x80, 0x41, 0x42, 0x43, 0x44])
        + bytes(range(32))
        + b"ABCD1234"
        + bytes([0x7F, 0x80, 0xFE, 0xFF])
        + (b"Z" * 32)
        + b"\x00\x07\x08\x09\x0a\x0b\x0c\x0d"
    )
    dump_empty = bdir / "dump_empty.bin"
    dump_empty.write_bytes(b"")
    for hd_fmt_str in (
        '1/1 "%c" 1/1 "%_c " 1/1 "%_p" 1/1 "%_u " "\\n"',
        '1/1 "%_u " "\\n"',
        '"%c" "%_c" "%_p" "%_u" "%d" "\\n"',
        '1/1 "%-4.2s" 4/1 "%02x" "\\n"',
        '"%.12s" 4/1 "%d " "\\n"',
        '1/1 "%d " 1/2 "%ld " 1/4 "%lld " 1/8 "%lld\\n"',
        '1/1 "%u " 1/2 "%u " 1/4 "%x\\n"',
        '1/4 "%f " 1/8 "%e\\n"',
        '"%_ad: " 16/1 "%02x " "\\n" "end: %_Ax" "\\n"',
        '"%_ao: " 2/2 "%04x" "\\n"',
    ):
        assert_ok(run_bb(busybox, ["hexdump", "-e", hd_fmt_str, str(dump_bin)]))
    assert_ok(
        run_bb(
            busybox,
            ["hexdump", "-e", '16/1 "%02x " "\\n"', "-e", '"%02x "', str(dump_bin)],
        )
    )
    dump_dup = bdir / "dump_dup.bin"
    dump_dup.write_bytes((b"A" * 48) + (b"B" * 8))
    assert_ok(run_bb(busybox, ["hexdump", str(dump_dup)]))
    assert_ok(
        run_bb(
            busybox,
            ["hexdump", "-s", "4"],
            stdin_data=b"0123456789abcdef",
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["hexdump", "-s", "4", "-"],
            stdin_data=b"0123456789abcdef",
        )
    )
    assert_ok(run_bb(busybox, ["hexdump", "-s", "8", str(dump_empty), str(dump_bin)]))
    assert_ok(
        run_bb(
            busybox,
            ["hexdump", "-s", "200", str(dump_empty), str(dump_bin)],
            stdin_data=b"0123456789abcdef" * 20,
        )
    )
    assert_ok(run_bb(busybox, ["hexdump", "-n", "0", str(dump_empty)]))
    assert (
        run_bb(
            busybox,
            ["hexdump", str(bdir / "missing_hd_1.bin"), str(bdir / "missing_hd_2.bin")],
        ).returncode
        != 0
    )
    for bad_hd_fmt in (
        '1/3 "%d"',
        '1/4 "%lZ"',
        '"%s"',
        '"%.z"',
        '1/1 "%_aZ"',
        '1/1 "%_Z"',
        '1/1 "%Z"',
        '4/1 "%d %d"',
        '4x "%d"',
        '4/12x "%d"',
        "4/1 no_quote",
        '4/1 "unterminated',
    ):
        assert run_bb(busybox, ["hexdump", "-e", bad_hd_fmt, str(dump_bin)]).returncode != 0

    # ls: columnar, long, recursive, filetypes, quoting, sorting, symlinks, errors
    ls_dir = bdir / "ls_dir"
    ls_dir.mkdir()
    ls_sub = ls_dir / "sub_dir"
    ls_sub.mkdir()
    ls_empty_sub = ls_dir / "empty_sub"
    ls_empty_sub.mkdir()
    (ls_sub / "nested.txt").write_text("nested", encoding="utf-8")
    ls_exec = ls_dir / "exec_item.sh"
    ls_exec.write_text("#!/bin/sh\n", encoding="utf-8")
    ls_exec.chmod(0o755)
    ls_reg = ls_dir / "reg_item.c"
    ls_reg.write_text("int x;\n", encoding="utf-8")
    ls_reg.chmod(0o644)
    ls_old = ls_dir / "old_item.txt"
    ls_old.write_text("old_content_longer\n", encoding="utf-8")
    os.utime(ls_old, (1000, 1000))
    ls_hidden = ls_dir / ".hidden_item"
    ls_hidden.write_text("hidden\n", encoding="utf-8")
    ls_special = ls_dir / 'q"b\\bell\x07ctrl\x01hi\x7f'
    ls_special.write_text("s", encoding="utf-8")
    os.symlink(str(ls_reg), ls_dir / "sym_file")
    os.symlink(str(ls_sub), ls_dir / "sym_dir")
    os.symlink(str(ls_dir / "missing_target"), ls_dir / "sym_broken")
    ls_unread = bdir / "ls_unread_dir"
    ls_unread.mkdir()
    ls_unread.chmod(0o000)
    try:
        for ls_flags in (
            ["ls"],
            ["ls", "-1", str(ls_dir)],
            ["ls", "-C", "-w", "30", "reg_item.c", "old_item.txt"],
            ["ls", "-C", "-w", "40", str(ls_dir)],
            ["ls", "-C", "-w", "0", str(ls_dir)],
            ["ls", "-C", "-w", "9999", str(ls_dir)],
            ["ls", "-x", "-w", "40", "-i", "-s", str(ls_dir)],
            ["ls", "-C", "-q", "-w", "40", str(ls_dir)],
            ["ls", "-C", "-Q", "-w", "40", str(ls_dir)],
            ["ls", "-l", "-i", "-s", "-F", str(ls_dir)],
            ["ls", "-l", "-s", "-p", "-n", str(ls_dir)],
            ["ls", "-l", "-n", "-g", "--full-time", str(ls_dir)],
            ["ls", "-l", "/dev/null", str(ls_reg), str(ls_sub)],
            ["ls", "-l", str(ls_empty_sub)],
            ["ls", "-a", "-R", str(ls_dir)],
            ["ls", "-A", str(ls_dir)],
            ["ls", "-A", "-R", str(ls_empty_sub)],
            ["ls", "-d", "-R", str(ls_dir), str(ls_sub)],
            ["ls", "--group-directories-first", "-S", "-r", str(ls_dir)],
            ["ls", "-t", "-u", str(ls_dir)],
            ["ls", "-t", "-c", str(ls_dir)],
            ["ls", "-X", str(ls_dir)],
            ["ls", "-H", str(ls_dir / "sym_dir")],
        ):
            assert_ok(run_bb(busybox, ls_flags, cwd=ls_dir))
        assert run_bb(busybox, ["ls", "-L", str(ls_dir)]).returncode != 0
        assert (
            run_bb(busybox, ["ls", "-L", str(ls_dir / "sym_broken"), str(ls_reg)]).returncode != 0
        )
        assert run_bb(busybox, ["ls", str(ls_dir / "does_not_exist_xyz")]).returncode != 0
        assert run_bb(busybox, ["ls", "-l", str(ls_dir / "does_not_exist_xyz")]).returncode != 0
        assert run_bb(busybox, ["ls", str(ls_unread)]).returncode != 0
    finally:
        ls_unread.chmod(0o755)

    # od (od_bloaty.c): format types, address radices, skip, strings, traditional mode, errors
    od_bin = bdir / "od_test.bin"
    od_bin.write_bytes(
        bytes(range(32))
        + b"\x7f\x80\xff\x00"
        + b"hello_printable_string\x00"
        + (b"L" * 160)
        + b"\x00"
        + (b"D" * 64)
        + b"tail!"
    )
    od_small = bdir / "od_small.bin"
    od_small.write_bytes(b"1234")
    od_empty = bdir / "od_empty.bin"
    od_empty.write_bytes(b"")
    for od_args in (
        ["od", "-a", "-b", "-c", "-d", "-D", "-f", "-h", "-H", "-i", "-I", "-l", "-L", str(od_bin)],
        ["od", "-o", "-O", "-B", "-x", "-X", "-s", "-v", str(od_bin)],
        [
            "od",
            "-t",
            "d1u1o1x1z",
            "-t",
            "d2u2d4u4d8u8",
            "-t",
            "dCdSdIdL",
            "-t",
            "fFfDfL",
            "-t",
            "f4f8",
            "-t",
            "d",
            "-t",
            "f",
            "-t",
            "ac",
            str(od_bin),
        ],
        ["od", "-A", "d", "-N", "20", "-j", "2", "-w8", str(od_bin)],
        ["od", "-A", "x", "-N", "8", "-j", "6", str(od_empty), str(od_small), str(od_bin)],
        ["od", "-A", "n", "-N", "0", str(od_bin)],
        ["od", "-N", "100", str(od_small)],
        ["od", "-S", "4", str(od_bin)],
        ["od", "-S", "150", str(od_bin)],
        ["od", "-S", "4", "-N", "45", str(od_bin)],
        ["od", "-S", "4", "-N", "2", str(od_bin)],
        ["od", "--traditional"],
        ["od", "--traditional", str(od_small)],
        ["od", "--traditional", "+2."],
        ["od", "--traditional", "+0x2", "+0X10"],
        ["od", "--traditional", str(od_bin), "0b"],
        ["od", "--traditional", str(od_bin), "2", "4"],
        ["od", "-A", "n", "--traditional", str(od_bin), "2", "4"],
    ):
        assert_ok(run_bb(busybox, od_args, stdin_data=b"0123456789abcdef"), "")
    assert_ok(run_bb(busybox, ["od", "-j", "4"], stdin_data=b"0123456789abcdef"))
    assert_ok(run_bb(busybox, ["od", "-j", "1100"], stdin_data=b"X" * 1050 + b"Y" * 100))
    assert (
        run_bb(
            busybox,
            ["od", "-j", "2", str(bdir / "od_missing.bin"), str(od_small)],
        ).returncode
        != 0
    )
    assert run_bb(busybox, ["od", str(bdir / "od_missing_only.bin")]).returncode != 0
    assert (
        run_bb(
            busybox,
            ["od", "-w3", str(od_small)],
        ).returncode
        == 0
    )
    assert (
        run_bb(
            busybox,
            ["od", "-w0", str(od_small)],
        ).returncode
        == 0
    )
    for bad_od_args in (
        ["od", "-A", "z", str(od_small)],
        ["od", "-t", "d3", str(od_small)],
        ["od", "-t", "d99", str(od_small)],
        ["od", "-t", "f3", str(od_small)],
        ["od", "-t", "f99", str(od_small)],
        ["od", "-t", "z", str(od_small)],
        ["od", "-j", "50", str(od_small)],
        ["od", "--traditional", "+", str(od_small)],
        ["od", "--traditional", "+2", "not_offset"],
        ["od", "--traditional", str(od_small), "not_offset"],
        ["od", "--traditional", str(od_small), "2", "not_offset"],
        ["od", "--traditional", str(od_small), "2", "4", "extra"],
    ):
        assert run_bb(busybox, bad_od_args).returncode != 0


def run_editors_and_find_suite(busybox: Path, work: Path) -> None:
    """Verify editors, math, and findutils applets.

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        work: Isolated temporary directory for test fixtures.
    """
    assert_ok(
        run_bb(
            busybox, ["awk", "-v", "my_var1=42", "-v", "_var=1", "BEGIN { print my_var1; exit }"]
        ),
        "42",
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", 'BEGIN { RS="END" } { print length($0) }'],
            stdin_data=(b"x" * 250) + b"END" + (b"y" * 10),
        ),
        "250",
    )
    assert run_bb(busybox, ["awk", "-v", "1bad=1", "BEGIN { exit }"]).returncode != 0
    assert run_bb(busybox, ["awk", "-v", "noval", "BEGIN { exit }"]).returncode != 0
    assert_ok(
        run_bb(
            busybox,
            ["sed", "s/world/busybox/"],
            stdin_data=f"hello world{NL}".encode(),
        ),
        "hello busybox",
    )
    grep_file = work / "grep.txt"
    grep_file.write_text(f"apple{NL}banana{NL}cherry{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["grep", "banana", str(grep_file)]), "banana")
    assert_ok(run_bb(busybox, ["egrep", "app|cher", str(grep_file)]), "apple")
    assert_ok(run_bb(busybox, ["fgrep", "banana", str(grep_file)]), "banana")
    assert run_bb(busybox, ["grep", "[", str(grep_file)]).returncode != 0
    grep_f2 = work / "grep2.txt"
    grep_f2.write_text(
        f"alpha{NL}foo_1 foo{NL}1foo{NL}_foo{NL}foo1{NL}foo_{NL}bar foo baz{NL}foo{NL}omega{NL}",
        encoding="utf-8",
    )
    grep_dash = work / "-dash_grep.txt"
    grep_dash.write_text(f"banana{NL}", encoding="utf-8")
    grep_pat = work / "grep_pat.txt"
    grep_pat.write_text(f"apple{NL}cherry{NL}", encoding="utf-8")
    grep_empty_pat = work / "grep_empty.txt"
    grep_empty_pat.write_text("", encoding="utf-8")
    grep_rdir = work / "grep_rdir"
    grep_rdir.mkdir()
    (grep_rdir / "sub").mkdir()
    (grep_rdir / "sub" / "item.txt").write_text(f"banana{NL}", encoding="utf-8")
    os.symlink(str(grep_rdir / "sub"), grep_rdir / "sym_dir")
    os.symlink(str(grep_file), grep_rdir / "sym_file")
    os.symlink(str(work / "missing_grep_target"), grep_rdir / "sym_broken")
    grep_unread = grep_rdir / "unread.txt"
    grep_unread.write_text(f"banana{NL}", encoding="utf-8")
    grep_unread.chmod(0o000)
    try:
        for g_args in (
            ["grep", "-F", "-i", "BANANA", str(grep_file)],
            ["grep", "-F", "-x", "banana", str(grep_file)],
            ["grep", "-F", "-x", "ban", str(grep_file)],
            ["grep", "-F", "-x", "ana", str(grep_file)],
            ["grep", "-F", "-w", "foo", str(grep_f2)],
            ["grep", "-x", "banana", str(grep_file)],
            ["grep", "-x", "ban", str(grep_file)],
            ["grep", "-x", "anana", str(grep_file)],
            ["grep", "-w", "foo", str(grep_f2)],
            ["grep", "-w", "^", str(grep_f2)],
            ["grep", "-q", "banana", str(grep_file)],
            ["grep", "-l", "banana", str(grep_file), str(grep_f2)],
            ["grep", "-L", "banana", str(grep_file), str(grep_f2)],
            ["grep", "-c", "-H", "banana", str(grep_file), str(grep_f2)],
            ["grep", "-c", "-h", "banana", str(grep_file), str(grep_f2)],
            ["grep", "-n", "-H", "-C", "1", "apple", str(grep_file)],
            ["grep", "-C", "1", "-A", "1", "-B", "1", "apple|cherry", "-E", str(grep_file)],
            ["grep", "-C", "1", "-E", "alpha|omega", str(grep_f2)],
            ["grep", "-B", "1", "apple", str(grep_f2), str(grep_file)],
            ["grep", "-B", "2", "foo", str(grep_f2)],
            ["grep", "-m", "1", "-A", "1", "foo", str(grep_f2)],
            ["grep", "-m", "1", "-A", "1", "apple", str(grep_file)],
            ["grep", "-F", "-o", "banana", str(grep_file)],
            ["grep", "-F", "-o", "-v", "banana", str(grep_file)],
            ["grep", "-v", "-o", "-A", "1", "banana", str(grep_file)],
            ["grep", "-o", "a", str(grep_file)],
            ["grep", "-o", "a*", str(grep_file)],
            ["grep", "-f", str(grep_pat), str(grep_file)],
            ["grep", "-f", str(grep_empty_pat), str(grep_file)],
            ["grep", "-x", "-f", str(grep_empty_pat), str(grep_file)],
            ["grep", "-e", f"apple{NL}banana", str(grep_file)],
            ["grep", "-r", "banana", str(grep_rdir), str(grep_file), str(work / "missing_rdir")],
            ["grep", "-r", "-s", "banana", str(grep_rdir)],
            ["grep", "-R", "-h", "-s", "banana", str(grep_rdir)],
            ["grep", "banana", "--", "-dash_grep.txt"],
            ["grep", "banana", str(work / "missing_grep.txt"), str(grep_file)],
            ["grep", "-s", "banana", str(work / "missing_grep.txt"), str(grep_file)],
        ):
            run_bb(busybox, g_args, cwd=work)
        assert_ok(run_bb(busybox, ["grep", "banana"], stdin_data=f"banana{NL}".encode()))
        assert_ok(run_bb(busybox, ["grep", "banana", "-"], stdin_data=f"banana{NL}".encode()))
        assert run_bb(busybox, ["grep"]).returncode != 0
    finally:
        grep_unread.chmod(0o644)

    f_a = work / "a.txt"
    f_b = work / "b.txt"
    f_a.write_text(f"line1{NL}line2{NL}", encoding="utf-8")
    f_b.write_text(f"line1{NL}line2_mod{NL}", encoding="utf-8")
    assert_ok(run_bb(busybox, ["cmp", str(f_a), str(f_a)]))
    assert_ok(run_bb(busybox, ["cmp", str(f_a)], stdin_data=f_a.read_bytes()))
    assert_ok(run_bb(busybox, ["cmp", "-", "-"], stdin_data=b"same"))
    assert_ok(run_bb(busybox, ["cmp", "-n", "5", str(f_a), str(f_b)]))
    assert_ok(run_bb(busybox, ["cmp", str(f_a), str(f_a), "0"]))
    assert_ok(run_bb(busybox, ["cmp", "-s", str(f_a), str(f_a), "1", "1"]))
    assert_ok(run_bb(busybox, ["cmp", str(f_a), str(f_a), "100", "100"]))
    assert run_bb(busybox, ["cmp", str(f_a), str(f_b)]).returncode == 1
    assert run_bb(busybox, ["cmp", "-s", str(f_a), str(f_b)]).returncode == 1
    assert run_bb(busybox, ["cmp", "-l", str(f_a), str(f_b)]).returncode == 1
    assert run_bb(busybox, ["cmp", "-l", str(f_b), str(f_a)]).returncode == 1
    f_c = work / "c.txt"
    f_c.write_text(f"Xine1{NL}Yine2{NL}", encoding="utf-8")
    assert run_bb(busybox, ["cmp", "-l", str(f_a), str(f_c)]).returncode == 1
    diff_res = run_bb(busybox, ["diff", "-u", str(f_a), str(f_b)])
    assert diff_res.returncode == 1
    patch_file = work / "change.patch"
    patch_file.write_bytes(diff_res.stdout)
    patch_target = work / "patched.txt"
    patch_target.write_text(f"line1{NL}line2{NL}", encoding="utf-8")
    assert_ok(
        run_bb(
            busybox,
            ["patch", str(patch_target), str(patch_file)],
            cwd=work,
        )
    )
    assert "line2_mod" in patch_target.read_text(encoding="utf-8")

    # --- Extended diff.c coverage ---
    diff_rich_a = work / "diff_rich_a.txt"
    diff_rich_b = work / "diff_rich_b.txt"
    diff_rich_a.write_text(
        "".join(f"ctx_{i}{NL}" for i in range(60))
        + f"\tTab_A  Space\r{NL}OnlyA1{NL}OnlyA2{NL}"
        + "".join(f"mid_{i}{NL}" for i in range(10))
        + "".join(f"dup_{(i * 3) % 7}{NL}" for i in range(40))
        + "".join(f"suf_{i}{NL}" for i in range(10))
        + "NoNewlineA",
        encoding="utf-8",
    )
    diff_rich_b.write_text(
        "".join(f"ctx_{i}{NL}" for i in range(60))
        + f"\ttab_a \tspace{NL}OnlyB1{NL}"
        + "".join(f"mid_{i}{NL}" for i in range(10))
        + "".join(f"dup_{(i * 5) % 7}{NL}" for i in range(40))
        + "".join(f"suf_{i}{NL}" for i in range(10))
        + "NoNewlineB",
        encoding="utf-8",
    )
    for d_args in (
        ["diff", "-d", "-b", "-i", "-t", "-T", str(diff_rich_a), str(diff_rich_b)],
        ["diff", "-w", "-i", "-B", "-U", "1", str(diff_rich_a), str(diff_rich_b)],
        ["diff", "-B", "-q", str(diff_rich_a), str(diff_rich_b)],
        ["diff", "-s", str(f_a), str(f_a)],
        ["diff", "-s", str(f_a), str(patch_file)],
        ["diff", "-L", "lbl1", "-L", "lbl2", str(f_a), str(f_b)],
        ["diff", "-L", "lbl1", str(f_a), str(f_b)],
    ):
        run_bb(busybox, d_args, cwd=work)
    # Hash collision in diff.c: "\x01\x01" and "\x00\x80" have identical hash (0) when read with -a
    diff_col_a = work / "diff_col_a.txt"
    diff_col_b = work / "diff_col_b.txt"
    diff_col_a.write_bytes(b"diffA\n\x01\x01\n\x01\x01\ndiffB\n")
    diff_col_b.write_bytes(b"diffX\n\x00\x80\n\x01\x01\x02\ndiffY\n")
    assert run_bb(busybox, ["diff", "-a", str(diff_col_a), str(diff_col_b)]).returncode == 1
    # Merge context in diff.c across adjacent and separated hunks
    diff_ct_a = work / "diff_ct_a.txt"
    diff_ct_b = work / "diff_ct_b.txt"
    diff_ct_a.write_text(f"a1{NL}c1{NL}c2{NL}c3{NL}a2{NL}suf1{NL}suf2{NL}", encoding="utf-8")
    diff_ct_b.write_text(
        f"b1{NL}"
        + "".join(f"ins_{i}{NL}" for i in range(10))
        + f"c1{NL}c2{NL}c3{NL}b2{NL}suf1{NL}suf2{NL}",
        encoding="utf-8",
    )
    assert run_bb(busybox, ["diff", "-U", "1", str(diff_ct_a), str(diff_ct_b)]).returncode == 1
    assert run_bb(busybox, ["diff", "-U", "2", str(diff_ct_a), str(diff_ct_b)]).returncode == 1
    assert run_bb(busybox, ["diff", "-U", "1", str(diff_ct_b), str(diff_ct_a)]).returncode == 1
    assert run_bb(busybox, ["diff", "-U", "2", str(diff_ct_b), str(diff_ct_a)]).returncode == 1
    diff_suf_a = work / "diff_suf_a.txt"
    diff_suf_b = work / "diff_suf_b.txt"
    diff_suf_a.write_text(f"s1{NL}s2{NL}", encoding="utf-8")
    diff_suf_b.write_text(f"extra{NL}s1{NL}s2{NL}", encoding="utf-8")
    assert run_bb(busybox, ["diff", str(diff_suf_a), str(diff_suf_b)]).returncode == 1
    assert run_bb(busybox, ["diff", str(diff_suf_b), str(diff_suf_a)]).returncode == 1
    diff_spc_a = work / "diff_spc_a.txt"
    diff_spc_b = work / "diff_spc_b.txt"
    diff_spc_a.write_text(f"   abc   def   {NL}{NL}   {NL}end", encoding="utf-8")
    diff_spc_b.write_text(f" abc\tdef\t{NL}{NL}\t{NL}end   ", encoding="utf-8")
    assert_ok(run_bb(busybox, ["diff", "-b", "-s", str(diff_spc_a), str(diff_spc_b)]))
    assert_ok(run_bb(busybox, ["diff", "-w", "-B", "-s", str(diff_spc_a), str(diff_spc_b)]))
    diff_boe_a = work / "diff_boe_a.txt"
    diff_boe_b = work / "diff_boe_b.txt"
    diff_boe_a.write_text(f"{NL}{NL}mid{NL}{NL}{NL}", encoding="utf-8")
    diff_boe_b.write_text(f"mid{NL}", encoding="utf-8")
    run_bb(busybox, ["diff", "-B", str(diff_boe_a), str(diff_boe_b)])
    run_bb(busybox, ["diff", "-B", str(diff_boe_b), str(diff_boe_a)])
    diff_bin_a = work / "diff_bin_a.bin"
    diff_bin_b = work / "diff_bin_b.bin"
    diff_bin_a.write_bytes(b"hello\x00world\n")
    diff_bin_b.write_bytes(b"hello\x00there\n")
    assert run_bb(busybox, ["diff", str(diff_bin_a), str(diff_bin_b)]).returncode == 1
    assert run_bb(busybox, ["diff", "-a", str(diff_bin_a), str(diff_bin_b)]).returncode == 1
    diff_bin_c = work / "diff_bin_c.bin"
    diff_bin_c.write_bytes(b"helloxworld\n")
    assert run_bb(busybox, ["diff", str(diff_bin_c), str(diff_bin_a)]).returncode == 1
    assert_ok(run_bb(busybox, ["diff", "-s", "-", "-"], stdin_data=b"same\n"))
    assert_ok(
        run_bb(busybox, ["diff", "-s", str(f_a), "-"], stdin_data=f_a.read_bytes()),
        "identical",
    )
    assert run_bb(busybox, ["diff", "-", str(f_b)], stdin_data=f_a.read_bytes()).returncode == 1
    assert run_bb(busybox, ["diff", "-N", str(f_a), str(work / "no_such_diff.txt")]).returncode == 1
    assert run_bb(busybox, ["diff", "-N", str(work / "no_such_diff.txt"), str(f_a)]).returncode == 1
    assert_ok(
        run_bb(
            busybox,
            ["diff", "-N", "-s", str(work / "no_such_1.txt"), str(work / "no_such_2.txt")],
        )
    )
    assert run_bb(busybox, ["diff", str(f_a), str(work / "no_such_diff.txt")]).returncode != 0
    assert run_bb(busybox, ["diff", "-U", "-1", str(f_a), str(f_b)]).returncode != 0
    diff_dir_sub = work / "diff_dir_sub"
    diff_dir_sub.mkdir(exist_ok=True)
    (diff_dir_sub / "a.txt").write_bytes(f_b.read_bytes())
    assert run_bb(busybox, ["diff", str(f_a), str(diff_dir_sub)]).returncode == 1
    assert run_bb(busybox, ["diff", "a.txt", str(diff_dir_sub)], cwd=work).returncode == 1
    assert run_bb(busybox, ["diff", str(diff_dir_sub), str(f_a)]).returncode == 1
    assert run_bb(busybox, ["diff", "-", str(diff_dir_sub)], stdin_data=b"x\n").returncode != 0
    assert run_bb(busybox, ["diff", str(diff_dir_sub), "-"], stdin_data=b"x\n").returncode != 0
    assert run_bb(busybox, ["diff", str(diff_dir_sub), str(work)]).returncode != 0
    diff_unread_file = work / "diff_unread_dir"
    diff_unread_file.mkdir(exist_ok=True)
    (diff_unread_file / "a.txt").write_bytes(b"secret\n")
    (diff_unread_file / "a.txt").chmod(0o000)
    try:
        run_bb(busybox, ["diff", str(f_a), str(diff_unread_file)])
    finally:
        (diff_unread_file / "a.txt").chmod(0o644)
    diff_fifo = work / "diff_fifo"
    os.mkfifo(str(diff_fifo))
    writer_proc = subprocess.Popen(
        [sys.executable, "-c", f"open({str(diff_fifo)!r}, 'wb').write(b'line1\\nline2\\n')"]
    )
    try:
        assert_ok(run_bb(busybox, ["diff", "-s", str(diff_fifo), str(f_a)]))
    finally:
        writer_proc.wait(timeout=3.0)
        diff_fifo.unlink(missing_ok=True)

    # --- Extended patch.c coverage ---
    p_dir = work / "patch_work"
    p_dir.mkdir(exist_ok=True)
    p_f1 = p_dir / "f1.txt"
    p_f1.write_text(f"a{NL}b{NL}c{NL}d{NL}e{NL}f{NL}", encoding="utf-8")
    p_multi = (
        f"garbage header line{NL}"
        f"--- a/dir1//f1.txt\\t2020-01-01{NL}"
        f"+++ b/dir1//f1.txt\t2020-01-01{NL}"
        f"@@ -1,2 +1,2 @@{NL}"
        f"+a_mod{NL}"
        f"-a{NL}"
        f" b{NL}"
        f"@@ -4,3 +4,4 @@{NL}"
        f" d{NL}"
        f"+d_added{NL}"
        f" e{NL}"
        f" f{NL}"
    ).encode()
    p_eof_bs = (
        f"--- f1.txt{NL}+++ f1.txt{NL}@@ -6 +6 @@{NL}-f{NL}+f_eof{NL}\\ No newline at end of file"
    ).encode()
    assert_ok(run_bb(busybox, ["patch", "--dry-run"], cwd=p_dir, stdin_data=p_eof_bs))
    assert_ok(run_bb(busybox, ["patch", str(p_f1), "--dry-run"], cwd=p_dir, stdin_data=p_eof_bs))
    assert_ok(
        run_bb(
            busybox,
            ["patch", "-p2", "--dry-run", "-i", "-"],
            cwd=p_dir,
            stdin_data=p_multi,
        )
    )
    assert_ok(run_bb(busybox, ["patch", "-p2"], cwd=p_dir, stdin_data=p_multi))
    # Forward (-N) on already applied hunk -> dummy_revert
    p_already = (f"--- f1.txt{NL}+++ f1.txt{NL}@@ -1,2 +1,2 @@{NL}+a_mod{NL}-a{NL} b{NL}").encode()
    assert_ok(run_bb(busybox, ["patch", "-N"], cwd=p_dir, stdin_data=p_already))
    # Without -N on already applied patch -> Possibly reversed hunk & fail_hunk
    assert run_bb(busybox, ["patch", "-p2"], cwd=p_dir, stdin_data=p_multi).returncode == 1
    assert (
        run_bb(busybox, ["patch", "-p2", "--dry-run"], cwd=p_dir, stdin_data=p_multi).returncode
        == 1
    )
    # Reverse (-R) back to original
    assert_ok(run_bb(busybox, ["patch", "-p2", "-R"], cwd=p_dir, stdin_data=p_multi))
    # Partial context match (buffered lines flushed and rechecked with multi-line overlap)
    p_f2 = p_dir / "f2.txt"
    p_f2.write_text(f"x{NL}x{NL}x{NL}y{NL}z{NL}{NL}w{NL}", encoding="utf-8")
    p_partial = (
        f"+++ f2.txt{NL}"
        f"@@ -1,6 +1,6 @@{NL}"
        f" x{NL}"
        f" x{NL}"
        f" y{NL}"
        f"-z{NL}"
        f"+z_new{NL}"
        f"\\ No newline at end of file{NL}"
        f"{NL}"
        f" w{NL}"
    ).encode()
    assert_ok(run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_partial))
    # Backwarn inside inner loop (buffered multi-line context recheck hits + line)
    p_f3 = p_dir / "f3.txt"
    p_f3.write_text(f"x{NL}x{NL}already_added{NL}mismatch{NL}", encoding="utf-8")
    p_bw_inner = (
        f"--- f3.txt{NL}"
        f"+++ f3.txt{NL}"
        f"@@ -1,3 +1,7 @@{NL}"
        f" x{NL}"
        f"+other_added{NL}"
        f"+already_added{NL}"
        f"+already_added{NL}"
        f" x{NL}"
        f" y{NL}"
        f"+trailing_added{NL}"
    ).encode()
    assert run_bb(busybox, ["patch", "-N"], cwd=p_dir, stdin_data=p_bw_inner).returncode == 1
    p_f3.write_text(f"x{NL}x{NL}already_added{NL}mismatch{NL}", encoding="utf-8")
    assert run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_bw_inner).returncode == 1
    p_f3.write_text(f"x{NL}x{NL}z{NL}", encoding="utf-8")
    p_inner_trailing_plus = (
        f"--- f3.txt{NL}+++ f3.txt{NL}@@ -1,2 +1,3 @@{NL} x{NL} z{NL}+added_end{NL}"
    ).encode()
    assert_ok(run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_inner_trailing_plus))
    assert_ok(run_bb(busybox, ["patch", "-R"], cwd=p_dir, stdin_data=p_inner_trailing_plus))
    # Outer loop multiple + lines when backwarn already set (without -N)
    p_f3.write_text(f"ctx1{NL}already_added{NL}mismatch{NL}", encoding="utf-8")
    p_bw_outer2 = (
        f"--- f3.txt{NL}+++ f3.txt{NL}@@ -2,1 +2,2 @@{NL}+ctx1{NL}+ctx1{NL}-nonexistent_line1{NL}"
    ).encode()
    assert run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_bw_outer2).returncode == 1
    # Hunk with matcheof == 0 failing at EOF (trailing context >= leading context)
    p_f3.write_text(f"ctx1{NL}already_added{NL}mismatch{NL}", encoding="utf-8")
    p_matcheof0_fail = (
        f"--- f3.txt{NL}"
        f"+++ f3.txt{NL}"
        f"@@ -2,2 +2,2 @@{NL}"
        f"-missing_line{NL}"
        f"+replacement_line{NL}"
        f" trailing_ctx{NL}"
    ).encode()
    assert run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_matcheof0_fail).returncode == 1
    # Non-empty buf at EOF failing hunk
    p_f3.write_text(f"ctx1{NL}already_added{NL}mismatch{NL}", encoding="utf-8")
    p_damaged = (
        f"--- f3.txt\\{NL}"
        f"+++ f3.txt\\{NL}"
        f"@@ -1,3 +1,3 @@{NL}"
        f" ctx1{NL}"
        f" already_added{NL}"
        f" mismatch{NL}"
        f"-missing_at_eof{NL}"
        f"+replacement{NL}"
    ).encode()
    assert run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_damaged).returncode == 1
    # Creating new file in subdir, dry-run creating, and deleting/truncating empty file
    p_create_del = (
        f"--- /dev/null\t2026-01-01{NL}"
        f"+++ b/new_sub/created.txt\t2026-01-01{NL}"
        f"@@ -0,0 +1,1 @@{NL}"
        f"+created_line{NL}"
    ).encode()
    p_epoch_del = (
        f"--- a/new_sub/created.txt\t2026-01-01{NL}"
        f"+++ b/new_sub/created.txt\t1970-01-01{NL}"
        f"@@ -1,1 +0,0 @@{NL}"
        f"-created_line{NL}"
    ).encode()
    p_del_nonempty = (
        f"--- a/new_sub/created.txt\t2026-01-01{NL}"
        f"+++ b/new_sub/created.txt\t2026-01-01{NL}"
        f"@@ -1,1 +1,0 @@{NL}"
        f"-created_line{NL}"
    ).encode()
    assert_ok(run_bb(busybox, ["patch", "-p1", "--dry-run"], cwd=p_dir, stdin_data=p_create_del))
    assert_ok(run_bb(busybox, ["patch", "-p1"], cwd=p_dir, stdin_data=p_create_del))
    assert (p_dir / "new_sub" / "created.txt").read_text(encoding="utf-8") == f"created_line{NL}"
    assert_ok(run_bb(busybox, ["patch", "-p1", "--dry-run"], cwd=p_dir, stdin_data=p_del_nonempty))
    # Truncate empty without -E, then delete with -E (using p_epoch_del and -R on creation patch)
    assert_ok(run_bb(busybox, ["patch", "-p1", "--dry-run"], cwd=p_dir, stdin_data=p_epoch_del))
    assert_ok(run_bb(busybox, ["patch", "-p1"], cwd=p_dir, stdin_data=p_epoch_del))
    assert (p_dir / "new_sub" / "created.txt").read_text(encoding="utf-8") == ""
    assert_ok(
        run_bb(busybox, ["patch", "-p1", "-E", "--dry-run"], cwd=p_dir, stdin_data=p_epoch_del)
    )
    assert_ok(run_bb(busybox, ["patch", "-p1", "-E"], cwd=p_dir, stdin_data=p_epoch_del))
    assert not (p_dir / "new_sub" / "created.txt").exists()
    # Create file in current directory (without slash in name)
    p_create_noslash = (
        f"--- /dev/null\t2026-01-01{NL}"
        f"+++ created_flat.txt\t2026-01-01{NL}"
        f"@@ -0,0 +1,1 @@{NL}"
        f"+flat_line{NL}"
    ).encode()
    assert_ok(run_bb(busybox, ["patch"], cwd=p_dir, stdin_data=p_create_noslash))
    # -p prefix > slashes in path (skips opening file)
    assert_ok(run_bb(busybox, ["patch", "-p5"], cwd=p_dir, stdin_data=p_create_del))
    # Really? @@ -0,0 +0,0 @@ error
    assert (
        run_bb(
            busybox,
            ["patch"],
            cwd=p_dir,
            stdin_data=f"--- a{NL}+++ b{NL}@@ -0,0 +0,0 @@{NL}".encode(),
        ).returncode
        != 0
    )

    # --- Extended ed.c coverage ---
    ed_file = work / "ed_doc.txt"
    ed_file.write_text(f"hello{NL}", encoding="utf-8")
    ed_script = f"1s/hello/ed_updated/{NL}a{NL}appended_line{NL}.{NL}w{NL}q{NL}".encode()
    assert_ok(run_bb(busybox, ["ed", str(ed_file)], cwd=work, stdin_data=ed_script))
    ed_ctrl = work / "ed_ctrl.txt"
    ed_ctrl.write_bytes(b"a\\b\x01\x07\x08\x09\x0b\x0c\x0d\x7f\x80\x9b\xff\n")
    assert_ok(run_bb(busybox, ["ed", str(ed_ctrl)], cwd=work, stdin_data=f"1l{NL}q{NL}".encode()))
    assert_ok(run_bb(busybox, ["ed"], cwd=work, stdin_data=b""))
    ed_contents = ed_file.read_text(encoding="utf-8")
    assert "ed_updated" in ed_contents
    assert "appended_line" in ed_contents

    ed_no_nl = work / "ed_no_nl.txt"
    ed_no_nl.write_bytes(b"line_without_nl")
    ed_big = work / "ed_big.txt"
    ed_big.write_bytes((b"L" * 70000 + b"\n") * 2)
    ed_w_out = work / "ed_w_out.txt"
    ed_full_script = NL.join(
        [
            "",
            "# comment",
            "f",
            "fx",
            f"f {ed_w_out}",
            "f",
            "=",
            "p",
            ".",
            "-",
            "a",
            "ab_ab_ac",
            "line_two",
            "line_three",
            "line_four",
            "line_five",
            "line_six",
            ".",
            "1.",
            ".",
            "-",
            "1",
            "-",
            "$",
            "",
            "1",
            "",
            "z",
            "z-",
            "z.",
            "1i",
            "inserted_top",
            ".",
            "2,3c",
            "changed_2_3",
            ".",
            "1,2p",
            "1,2l",
            "1=",
            "2",
            ".+1,$-1p",
            ",p",
            "1,p",
            "1,xyz",
            "'A",
            "1kA",
            "1k",
            "1kab",
            "1ka",
            "2kb",
            "'a,'bp",
            "//",
            "/nonexistent_pat/",
            "/ab_ac/",
            "//",
            "/line",
            "999p",
            "0p",
            "3,1p",
            "999a",
            "bad_insert_line",
            ".",
            "rx",
            "r",
            "r /nonexistent_ed_file_xyz",
            f"r {work}",
            f"r {ed_no_nl}",
            "1l",
            "$l",
            f"1r {ed_big}",
            "999r /etc/hosts",
            "s",
            "s/none",
            "s/none/x/",
            "s/line/LINE/z",
            "s/line/LINE",
            "s/LINE/L/g",
            "s/L/L/p",
            "s/L/line/gp",
            "s/line/LONGER_LINE/g",
            "s/LONGER_LINE/SHORTER/p",
            "s//SAME_LEN/p",
            "wx",
            "w /nonexistent_dir_xyz/file",
            f"1,2w {ed_w_out}",
            f"w {ed_w_out}",
            "2",
            "2d",
            "$",
            "$d",
            "2",
            "1d",
            "1,$d",
            "i",
            "from_empty_i",
            ".",
            "1,$d",
            "p",
            "d",
            "s/a/b/",
            "w",
            "/pat/",
            "a",
            "dirty_line",
            ".",
            "1q",
            "qx",
            "q",
            "n",
            "q",
            "y",
        ]
    ).encode("utf-8")
    assert_ok(run_bb(busybox, ["ed"], cwd=work, stdin_data=ed_full_script))
    assert_ok(
        run_bb(
            busybox,
            ["ed"],
            cwd=work,
            stdin_data=(
                f"w{NL}"
                f"1i{NL}fail_empty_1i{NL}.{NL}"
                f"i{NL}.not_dot{NL}.{NL}"
                f"r {ed_file}{NL}"
                f"a{NL}x_x{NL}x_x{NL}.{NL}"
                f"s//y/{NL}s /x/y/{NL}"
                f"s/x/Y/{NL}"
                f".{NL}"
                f"1{NL}"
                f"{NL}"
                f"1s/Y/y/p{NL}"
                f"1,$s/x/Y/g{NL}"
                f"1,$s/Y/Z/p{NL}"
                f"1,$s/Z/LONGER_Z/p{NL}"
                f"1s/LONGER_Z/Z/{NL}"
                f"1,/nonexistent_pat/p{NL}"
                f"1i{NL}ins_with_addr{NL}.{NL}"
                f"w {ed_w_out}{NL}"
                f"unknown_cmd{NL}"
                f"a{NL}unterminated"
            ).encode(),
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["ed", "-s"],
            cwd=work,
            stdin_data=f"r {ed_file}{NL}w {ed_w_out}{NL}a{NL}dirty_eof{NL}.{NL}q{NL}".encode(),
        )
    )
    assert_ok(run_bb(busybox, ["ed", str(work / "nonexistent_initial_ed.txt")], cwd=work))

    # --- Extended sed.c coverage ---
    sed_w1 = work / "sed_w1.txt"
    sed_w2 = work / "sed_w2.txt"
    sed_r1 = work / "sed_r1.txt"
    sed_r1.write_text(f"read_line_1{NL}read_line_2{NL}", encoding="utf-8")
    sed_cmd_file = work / "sed_cmds.sed"
    sed_cmd_file.write_text(
        f"#n comment{NL}# regular comment{NL}s/foo/bar/g  ; p{NL}",
        encoding="utf-8",
    )
    assert_ok(run_bb(busybox, ["sed", "--version"]), "not GNU sed")
    assert_ok(
        run_bb(
            busybox,
            ["sed", "-f", str(sed_cmd_file), "-e", "#comment\ns/bar/BAR/p"],
            stdin_data=f"foo{NL}".encode(),
        ),
        "bar",
    )
    # Multi-line command continuation with trailing backslash, r/w with glued newline
    assert_ok(
        run_bb(
            busybox,
            [
                "sed",
                "-e",
                "s/a/b\\",
                "-e",
                "c\\",
                "-e",
                "d/",
                "-e",
                f"w {sed_w1}\n#comment",
                "-e",
                f"s/b/B/w {sed_w1}\np",
                "-e",
                f"s/B/C/w {sed_w2}",
            ],
            stdin_data=f"a{NL}".encode(),
        ),
        f"C{NL}c{NL}d",
    )
    assert_ok(
        run_bb(busybox, ["sed", "-r", "-E", "s/(a|b)+/X/"], stdin_data=f"ab{NL}".encode()), "X"
    )
    # Escapes, bracket expressions, delimiters, flags (g, p, i, I, numbers, #, ;, })
    for sed_expr, sed_in, sed_exp in (
        (r"s/[a]/X/g", f"ab{NL}", "Xb"),
        (r"s/[ab]/X/g", f"ab{NL}", "XX"),
        (r"s/[]a]/X/g", f"]a{NL}", "XX"),
        (r"s/[^]a]/X/g", f"ba]{NL}", "Xa]"),
        (r"s/[a^]/X/g", f"a^{NL}", "XX"),
        (r"/nonexistent_addr/s/a/X/", f"a{NL}", "a"),
        (r"s+a\+b+\t\r\n\\+", f"a+b{NL}", "\t\n\\"),
        (r"s&a&\&\1&", f"a{NL}", "&"),
        (r"s/\(a\)\(b\)*/\1-\2-\9-\\x-&/", f"a{NL}", "a---\\x-a"),
        (r"s/a/X/2", f"aaa{NL}", "aXa"),
        (r"s/a*/X/2", f"bbb{NL}", "bXbb"),
        (r"s/a*/X/2", f"b{NL}", "b"),
        (r"s/a*/X/2", f"{NL}", ""),
        (r"s/a*/X/g", f"aaab{NL}", "XbX"),
        (r"s/^/X/g", f"abc{NL}", "Xabc"),
        (r"s/^/X/2", f"abc{NL}", "Xabc"),
        (r"s/a/X/iIp #comment", f"A{NL}", "X"),
        (r"{s/a/b/;s/b/c/}", f"a{NL}", "c"),
        (r"/a/s//X/", f"a{NL}", "X"),
        (r"\,a,s,,X,", f"a{NL}", "X"),
        (r"/a/{//p}", f"a{NL}", "a"),
        ("1,+1s/a/X/", f"a{NL}a{NL}a{NL}", f"X{NL}X{NL}a"),
        ("1,2s/a/X/", f"a{NL}a{NL}a{NL}", f"X{NL}X{NL}a"),
        ("1,$s/a/X/", f"a{NL}b{NL}", f"X{NL}b"),
        ("1,/end/s/x/X/", f"start x{NL}end x{NL}after x{NL}", "after x"),
        ("$s/a/X/", f"a{NL}a{NL}", f"a{NL}X"),
        ("/start/,/end/s/x/X/", f"before x{NL}start x{NL}mid x{NL}end x{NL}after x{NL}", "after x"),
        ("1!s/a/X/", f"a{NL}a{NL}", f"a{NL}X"),
        ("1!{s/a/X/; { s/X/Y/ } }", f"a{NL}a{NL}", f"a{NL}Y"),
        ("1{s/a/X/; { s/X/Y/ } }", f"b{NL}a{NL}", f"b{NL}a"),
        (
            f"1a\\{NL}appended\\t\\x{NL}1i\\{NL}inserted{NL}2c\\{NL}changed",
            f"1{NL}2{NL}",
            "inserted",
        ),
        ("1,2c changed_range", f"1{NL}2{NL}3{NL}", f"changed_range{NL}3"),
        (r"a\appended_inline", f"x{NL}", "appended_inline"),
        ("a\\", f"x{NL}", "x"),
        ("a\napp1\ni\\\nins1", f"x{NL}", "ins1"),
        (f"1r {sed_r1}{NL}1r /nonexistent_sed_r_xyz", f"line1{NL}", "read_line_1"),
        ("y/ab\\//AB_/", f"ab/{NL}", "AB_"),
        ("y///", f"ab{NL}", "ab"),
        ("=;N;P;D", f"l1{NL}l2{NL}", "1"),
        ("p;P;d", f"l1{NL}", "l1"),
        ("p;P;D", f"l1{NL}", ""),
        ("N;D", f"l1{NL}l2{NL}", "l2"),
        ("n;N;q", f"1{NL}2{NL}3{NL}4{NL}", "1"),
        ("n", f"only_one{NL}", "only_one"),
        ("G;g;x;g;G;h;H;x;g;G", f"a{NL}b{NL}", ""),
        ("H;g", f"a{NL}", "a"),
        (":lbl; s/a/b/; t end; T lbl; :end; b; b lbl", f"a{NL}", "b"),
        ("s/a/b/; T; t", f"a{NL}", "b"),
        ("s/a/b/; t; T", f"x{NL}a{NL}", "b"),
        ("s/a/" + ("Z" * 100) + "/", f"a{NL}", "ZZZZ"),
    ):
        assert_ok(run_bb(busybox, ["sed", sed_expr], stdin_data=sed_in.encode()), sed_exp)
    assert_ok(run_bb(busybox, ["sed", "-n", "n"], stdin_data=f"l1{NL}l2{NL}".encode()), "")

    # NUL-separated lines & non-EOL files across multiple inputs + '-' stdin
    sed_noeol1 = work / "sed_noeol1.txt"
    sed_noeol2 = work / "sed_noeol2.txt"
    sed_nul = work / "sed_nul.txt"
    sed_noeol1.write_bytes(b"noeol1")
    sed_noeol2.write_bytes(b"noeol2")
    sed_nul.write_bytes(b"nul1\x00nul2\x00")
    sed_multi_res = run_bb(
        busybox,
        [
            "sed",
            "s/noeol/NOEOL/",
            str(sed_noeol1),
            "-",
            str(sed_noeol2),
            str(sed_nul),
            str(work / "missing_sed_in.txt"),
        ],
        stdin_data=b"stdin_line\n",
    )
    assert sed_multi_res.returncode == 1
    assert b"NOEOL1\nstdin_line\nNOEOL2\nnul1\x00nul2\x00" in sed_multi_res.stdout
    # In-place (-i and -i.bak)
    sed_ip1 = work / "sed_ip1.txt"
    sed_ip2 = work / "sed_ip2.txt"
    sed_ip1.write_text(f"one{NL}two{NL}", encoding="utf-8")
    sed_ip2.write_text(f"one{NL}two{NL}", encoding="utf-8")
    run_bb(
        busybox,
        ["sed", "-i.bak", "1,+1s/o/O/", str(work / "missing_ip.txt"), str(sed_ip1), str(sed_ip2)],
    )
    assert (work / "sed_ip1.txt.bak").exists()
    assert "One" in sed_ip1.read_text(encoding="utf-8")
    assert_ok(run_bb(busybox, ["sed", "-i", "s/O/o/", str(sed_ip1)]))
    # Error paths in sed
    for bad_sed in (
        ["sed"],
        ["sed", "-i"],
        ["sed", "-i", "s/a/b/"],
        ["sed", "s"],
        ["sed", "s/a/b"],
        ["sed", "s/a/b/z"],
        ["sed", "s//b/"],
        ["sed", "//p"],
        ["sed", "1,+a"],
        ["sed", "1,p"],
        ["sed", "1!"],
        ["sed", "1,2a text"],
        ["sed", "1,2r file"],
        ["sed", "w"],
        ["sed", "l"],
        ["sed", "z"],
        ["sed", "2{p"],
        ["sed", "b no_such_label"],
        ["sed", "-i", "b no_such_label", str(sed_ip1)],
    ):
        assert run_bb(busybox, bad_sed, stdin_data=f"a{NL}".encode()).returncode != 0

    assert_ok(run_bb(busybox, ["vi", "-c", "q", str(ed_file)], cwd=work, stdin_data=b"\x1b[24;80R"))

    # --- Extended vi.c coverage ---
    vi_f1 = work / "vi_f1.txt"
    vi_f2 = work / "vi_f2.txt"
    vi_f1.write_text(
        "  \talpha ((beta)) [gamma] {delta} (unmatched\n"
        "second_line word1 word2  word3! === +++\n"
        "\n"
        "\n"
        "fifth_line\x01\x7f\x80_end\n"
        "    indented_space_line\n"
        "\tindented_tab_line\n" + "".join(f"line_{idx:02d}_text\n" for idx in range(8, 45)),
        encoding="latin-1",
    )
    vi_f2.write_text("file_two_line1\nfile_two_line2\n!", encoding="utf-8")

    # 1. Interactive motion, marks, registers, undo, search, replace, shift, join, scroll
    def _run_vi_piped(vi_args: list[str], chunks: tuple[bytes, ...]) -> None:
        """Feed keystroke chunks to ``vi`` over a pipe, pausing briefly after standalone ESC."""
        r_fd, w_fd = os.pipe()
        proc = subprocess.Popen(
            _bb_cmd(busybox, vi_args),
            cwd=work,
            stdin=r_fd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(r_fd)
        for chunk in chunks:
            os.write(w_fd, chunk)
            time.sleep(0.08 if chunk.endswith(b"\x1b") else 0.02)
        os.close(w_fd)
        proc.wait(timeout=15.0)
        assert proc.returncode == 0

    vi_chunks_1: tuple[bytes, ...] = (
        b"\x1b[24;80R"
        b"."  # '.' when lmc_len == 0
        b";,"  # dot_to_char when last_search_char == 0
        b"u"  # undo_pop when undo stack is empty ("Already at oldest change")
        b":set ai et fl ic sm ts=4\r"
        b"h"  # error at BOL with err_method == 1 -> calls flash(10)
        b":set all\r"
        b":set noai noet nofl noic nosm\r"
        b"0$\x1b[H\x1b[F"
        b"2$0^"
        b"10l3h3 "
        b"wWeEbB2w2b2W2B2e2E"
        b"0f(%"  # match ((beta))
        b"0f[%"
        b"0f{%"
        b"0f(f(f(%"  # unmatched '(' -> find_pair returns NULL -> indicate_error
        b"$%"  # no pair on newline -> indicate_error
        b"0f(F(;,"
        b"0t)$T(l;h,"
        b"0f)2f)"
        b"fZ"  # not found -> indicate_error
        b"ma'a''"
        b"'z"  # unset mark
        b"'\x01"  # invalid mark char
        b"m\x01"  # invalid mark char
        b'"ayy"ap"aP'
        b'"\x01'  # invalid reg char
        b'"bp'  # empty register
        b"YppP"
        b"yw2pP"
        b"y}p"  # multi-line non-WHOLE register put
        b"dduu"
        b"2x2XuU"
        b"sR\x1b",
        b"CW\x1b",
        b"0D"  # D on non-empty line, then D on empty line
        b"D.2."
        b"j0~3~u"  # ~ on lower/upper/non-alpha, then undo chained ~
        b"J2J"
        b"99GJJ"  # J on last line (dot == end - 1)
        b"gg"
        b">><<"
        b"6G<<>>"  # << on space-indented line
        b">j<k"
        b"<\x1b",
        b"d2w2d3wd0"  # get_motion_char with non-zero and zero counts, cmdcnt * cnt
        b"dZ"  # invalid motion char -> find_range returns -1 -> indicate_error
        b"d0"  # at BOL -> p == dot -> buftype = -1
        b"999dd"  # count past EOF -> do_cmd('j') sets cmd_error -> buftype = -1
        b"d999j"  # motion 'j' sets cmd_error -> buftype = -1
        b"2dl"
        b"cwNEW\x1b",
        b"cWNEW2\x1b",
        b"c$END\x1b",
        b"c0BEG\x1b",
        b"ccLINE\x1b",
        b"2ccLINES\x1b",
        b"4G0dwdW$dbdd2dddld d\x1b",
        b"5G0rX2rY99rZr\x1b",
        b"5|z.z-z\r99H2HM99L2Lgg2ggG3GgXg\x1b[A"
        b"10j"  # cnt <= (rows - 1) / 2 in sync_cursor
        b"25j\x19\x05"  # dot_scroll when dot is at bottom/top of screen
        b"H20\x05"  # dot_scroll forward past dot (dot < screenbegin)
        b"L20\x19"  # dot_scroll backward past dot (dot > end_screen)
        b"\x02\x04\x05\x06\x07\x0c\x12\x15\x19\x1b",
        b"\x1b[5~\x1b[6~\x1b[A\x1b[B\x1b[C\x1b[D\x1b[3~"
        b"G\x1b[3~"  # KEYCODE_DELETE at end - 1
        b"gg2+2-\r\n3j2k$j$k"
        b"999j999k"
        b"5Go\r\x1b",
        b"gg2}2{"
        b"3G$"  # get_column on line with control chars \x01 and \x7f
        b"2G$bB0f=wW"  # b and w on punctuation words === +++
        b"1G0l}"  # find_range '{}' with p != begin_line(p)
        b"/word\rn2nN/\r?line\rn2nN?\r"
        b"/nonexistent_vi_pat_xyz\r?nonexistent_vi_pat_xyz\r/\x08/ab\x08\x08\x08",
        b":\x1b",
        b"ihello\x16\x01\rworld\x08\x7f\x1b",
        b"7G0i\t\x1b",  # insert before a tab (sync_cursor cmd_mode && *d == '\t')
        b"aappend\x1b",
        b"Aend\x1b",
        b"Ibeg\x1b",
        b"obelow\x1b",
        b"Oabove\x1b",
        b"0Rreplace\x08\x08\x08\x08\x08\x08\x08\x08more\rpast_eol\x1b",
        b"0R\x1b[2~ins\x1b[2~rep\x1b",
        b":set ai et sm ts=4\ri\t()[unmatched])\x04\r  \x04\rx\r\x1b",
        b"O\tx\x1b",  # O with autoindent + expandtab + non-zero indent
        b"O\r\x1b",
        b":set noet\rO\tindent\x04\r\x1b",
        b"i\x04\x1b",  # Ctrl-D at column 0
        b"Gccnewcc\r\x1b",  # cc at EOF with autoindent
        b"Gyyjp5GdG3Gdgg\x01\x00\x7f\x80ZXZQ",
    )
    _run_vi_piped(["vi", "vi_f1.txt"], vi_chunks_1)

    # Focused mini-test on vi_f2.txt (ends with "!", init_text_buffer adds \n)
    vi_mini: tuple[bytes, ...] = (
        b"\x1b[24;80R"
        b"0~3~u"  # ~ on lower/upper and undo
        b":set flash\rfZ:set noflash\r"  # flash(10) when err_method != 0
        b"0f_w0f_b1G0b"  # w and b starting on '_', b at text[0]
        b"G0b"  # b on punctuation '!'
        b"G0dW"  # dW at EOF on punctuation '!' (!at_eof(dot) is false, ispunct(*dot) is true)
        b"i!\x1b",
        b"G0dw"  # dw at EOF on punctuation '!'
        b"ix\x1b",
        b"G0dw"  # dw at EOF on alpha 'x' (!at_eof(dot) is false, ispunct(*dot) is false)
        b"ifirst (mid) ) last\x1b",
        b"0f(%"  # % matching ( and )
        b"0f)l%"  # % on unmatched ')' -> p < text in find_pair and p == NULL in case '%'
        b"0f(F(;,"  # F and ; and ,
        b"0t)$T(l;h,"  # t and T and ; and ,
        b"0l2f)"  # 2f)
        b"1G0i)\x1b",
        b"1G0lT)"  # T) when ')' is at BOL -> dot_left() when dot[-1] == '\n'
        b"2G0h"  # h at BOL of line 2 (dot[-1] == '\n')
        b"1G0lB"  # B reaching text[0] -> p <= text in skip_thing
        b":set sm\r1G0i)\x1b",  # showmatching on unmatched ')' at text[0] -> q == NULL
        b":set nosm\r"
        b"1G+\r-"  # +, \r (c == 13), and -
        b"1G0i  x\r\x16\x7f\x16\x9b\x1b",  # << on space-indented line, 0x7f & 0x9b
        b"$"  # get_column on line with 0x7f and 0x9b
        b":l\r"  # :l on line with 0x7f and 0x9b
        b"\x9b\x7f"  # not_implemented -> print_literal with 0x9b and 0x7f
        b"1G<<$j"  # << on space-indented line, $j with cindex == C_END
        b"2G0X"  # X at BOL (dot[-1] == '\n')
        b"1G0i\t\x1b",  # insert tab at BOL
        b"1G0ix",  # enter insert mode before tab for refresh(FALSE) (tp == d - 1 && *d == '\t')
        b"\x1b",
        b":set ai et\r1G0i\t\rx\x08\x04\x1b",  # Ctrl-D with autoindent && indentcol
        b":set ai noet\r1G0i\t\rx\x1b",  # autoindent + !expandtab on newline
        b"1GJ"  # J when next line has leading whitespace
        b"1Go\rno_ws\x1b",
        b"1GJJ"  # J on empty line and line without leading whitespace
        b"1Go\r== x\x1b",
        b"3G2lh"  # place dot on second '='
        b"b"  # b on two-char punctuation "=="
        b"w"  # w on two-char punctuation "=="
        b"Go\ra b\x1b",
        b"G0dW"  # dW from 'a' to 'b' at EOF -> at_eof(dot) is true, ispunct(*dot) is false
        b"G$iab\x1b",
        b"G0dw"  # dw on 2-char word at EOF -> !at_eof(dot) is false, ispunct(*dot) is false
        b":1,1w vi_sub_w.txt\r"  # partial write from line 1 (q == text && q + l != end)
        b":a\n"  # '\n' in get_input_line
        b":a\x7f\r"  # 0x7f backspace in get_input_line
        b':"' + (b"a" * 125) + b"ZQ",  # get_input_line reaching MAX_INPUT_LEN - 1 (127)
    )
    _run_vi_piped(["vi", "vi_f2.txt"], vi_mini)

    # Scroll test on 50-line file for sync_cursor and dot_scroll
    vi_scroll_f = work / "vi_scroll.txt"
    vi_scroll_f.write_text(
        "para1_a\npara1_b\n\npara2_a\npara2_b\n\n" + "".join(f"sline_{i:02d}\n" for i in range(50)),
        encoding="utf-8",
    )
    _run_vi_piped(
        ["vi", "vi_scroll.txt"],
        (
            b"\x1b[24;80R1G0Rabc\x08\x7f\x08\x08\x1b",
            b"26G"  # 26 - 23 = 3 lines below end_scr (< 11) -> sync_cursor lines 861-870
            b"H20\x05"  # scroll forward 20 lines while dot is at H -> dot < screenbegin
            b"L20\x19"  # scroll backward 20 lines while dot is at L -> dot > end_screen()
            b"1G2}2{"
            b"1G0ld}4G0ld{"
            b"10G3dl"  # tmpcnt == dot - p in find_range
            b"1G/sline\r2n2N"  # search hit and cmdcnt > 1
            b":set ic\r/SLINE\rn:set noic\r"  # ignorecase in mycmp
            b"2G>>"  # >> on empty line (line 2 is blank after 1G0ld})
            b"ZQ",
        ),
    )

    # 2. Colon commands, address ranges, substitutions, read/write/next/prev/rewind
    vi_chunks_2: tuple[bytes, ...] = (
        b"\x1b[24;80R"
        b"ma"  # set mark 'a' for :'a
        b":\r"
        b': "comment\r'
        b":=\r"
        b":==\r"  # cmd[0] == '=' && cmd[1] != '\0'
        b":1=\r"
        b":1,3l\r"
        b":l\r"
        b":version\r"
        b":features\r \r"
        b":!true\r\n"
        b":!false\r\r"
        b":1!true\r"
        b":1file\r"
        b":file\r"
        b":s\r"
        b":s/none\r"  # !R -> goto colon_s_fail
        b":s/none/x/\r"
        b":%s/line/LINE/g\r"
        b":%s//line/g\r"
        b":1,2s/a//g\r"
        b":1s/l/L\r"  # no trailing delimiter (flags == NULL)
        b":1s/L/l/\r"
        b":.,.+1y\r"
        b":y\r"
        b":2d\r"
        b":d\r"
        b":0r vi_f2.txt\r"
        b":$r vi_f2.txt\r"
        b":r\r"  # !args[0] && current_filename != NULL
        b":r /nonexistent_vi_file_xyz\r"
        b":r .\r"
        b":1,2w vi_out_w.txt\r"
        b":w vi_out_w.txt\r"
        b":w! vi_out_w.txt\r"
        b":w vi_f1.txt\r"  # strcmp(fn, exp) == 0
        b":w /nonexistent_dir_xyz/file\r"
        b":999d\r"
        b":3,1d\r"
        b":-5,1d\r"  # b < 0 in colon
        b": 1 , 2 y\r"  # spaces in get_one_address and get_address
        b":,1y\r"  # !valid && *p == ','
        b":;1y\r"  # !valid && *p == ';'
        b":1,\r"  # !valid && *got & 1
        b":'a\r"
        b":'z\r"  # unset mark
        b":'1\r"  # c < 'a' in get_one_address
        b":'{\r"  # c > 'z' in get_one_address
        b"://\r"  # empty search pattern in get_one_address
        b":?LINE\r"  # backward search without closing '?' (*p != c)
        b":?LINE?\r"  # backward search in get_one_address
        b":?nonexistent_pat_xyz?\r"  # backward search wraparound in get_one_address
        b":1k\r"
        b":/line/\r"
        b":?line?\r"
        b":/nonexistent_pat_xyz/\r"
        b":1+1-1\r"
        b":+1\r"
        b":-1\r"
        b":1;2d\r"
        b":set\r"  # !args[0] in :set
        b":set badopt\r"
        b":set nx\r"  # argp[0] == 'n' && argp[1] != 'o'
        b":set no\r"  # argp[0] == 'n' && argp[1] == 'o' && !argp[2]
        b":set notabstop\r"
        b":set notabstop=4\r"  # eq && flg_no in VI_TABSTOP
        b":set ts\r"  # !eq in VI_TABSTOP
        b":set ts=0\r"
        b":set ts=999\r"
        b":set ai=1\r"
        b":xx\r"  # cmd[0] == 'x' && cmd[1] != '\0'
        b":unknown_colon_cmd\r"
        b":q\r"  # modified -> refuses
        b":e\r"  # modified -> refuses
        b":e!\r"  # modified + useforce -> reloads
        b":e vi_f2.txt\r"  # :e with filename when unmodified
        b":e! vi_f1.txt\r"
        b"iMOD\x1b",
        b":rew\r"  # modified -> refuses
        b":rew!\r"  # modified + useforce -> rewinds
        b"\x1b[24;80R"
        b":w!\r"
        b":x\r"  # unmodified :x with more files to edit -> refuses
        b":wq\r"  # refuses since more files to edit
        b":q\r"  # more files to edit -> refuses
        b":wn\r"  # writes and switches to next file (vi_f2)
        b"\x1b[24;80R"
        b"iMOD2\x1b",
        b":n!\r"  # useforce with *cmd == 'n' (advances to 3rd arg)
        b"\x1b[24;80R"
        b":n\r"  # *cmd == 'n' && n <= 0 -> "No more files to edit"
        b":prev\r"  # back to vi_f2
        b"\x1b[24;80R"
        b":prev\r"  # back to vi_f1
        b"\x1b[24;80R"
        b":prev\r"  # no previous files
        b":e % #\r"
        b":e \\%\r"
        b":rew!\r"
        b"\x1b[24;80R"
        b"ZZ"  # refuses since 2 more files to edit
        b":n\r"
        b"\x1b[24;80R"
        b":n\r"
        b"\x1b[24;80R"
        b"iEDIT\x1b",
        b"ZZ",  # modified_count != 0 on last file -> file_write and exit
    )
    _run_vi_piped(["vi", "vi_f1.txt", "vi_f2.txt", "vi_f2.txt"], vi_chunks_2)

    # Test :wq! with remaining files and ZZ error path (directory/unwritable filename)
    _run_vi_piped(
        ["vi", "vi_f1.txt", "vi_f2.txt"],
        (b"\x1b[24;80R:wq!\r",),
    )
    _run_vi_piped(
        ["vi"],
        (
            b"\x1b[24;80RiX\x1b",
            b"ZZ",  # file_write(NULL) returns -2
            b":f /nonexistent_dir_xyz/file\rZZ",  # file_write returns -1
            b"ZQ",
        ),
    )

    # Test narrow/wide screen dimensions, Hit_Return, buffer realloc > 10KB, signals, EOF
    big_file = work / "vi_big.txt"
    big_file.write_text("A" * 500 + "\n", encoding="utf-8")
    _run_vi_piped(
        ["vi", "vi_big.txt"],
        (
            b"\x1b[9999;9999R",  # columns > MAX_SCR_COLS, rows > MAX_SCR_ROWS
            b"ma",  # set mark 'a' so text_hole_make realloc adjusts mark[i]
            b"Y25p",  # 25 * 500 = 12500 bytes -> triggers text_hole_make xrealloc
            b":l\rZQ",  # status_buffer overflow (>200 chars)
        ),
    )
    # Test narrow 30-col screen so :set all triggers Hit_Return, followed by stdin EOF
    assert (
        run_bb(
            busybox,
            ["vi", "vi_f2.txt"],
            cwd=work,
            stdin_data=b"\x1b[24;30R:set all\r \n",
        ).returncode
        != 0
    )
    # Test SIGWINCH and SIGINT handlers in vi
    r_sig, w_sig = os.pipe()
    sig_proc = subprocess.Popen(
        _bb_cmd(busybox, ["vi", "vi_f2.txt"]),
        cwd=work,
        stdin=r_sig,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(r_sig)
    os.write(w_sig, b"\x1b[24;80R")
    time.sleep(0.06)
    sig_proc.send_signal(signal.SIGWINCH)
    time.sleep(0.03)
    os.write(w_sig, b"\x1b[24;80R")
    time.sleep(0.04)
    sig_proc.send_signal(signal.SIGINT)
    time.sleep(0.04)
    os.write(w_sig, b"ZQ")
    os.close(w_sig)
    sig_proc.wait(timeout=10.0)
    assert sig_proc.returncode == 0

    # 3. Readonly mode, no filename, EXINIT, ~/.exrc, -H, -h, large undo queue & buffer realloc
    assert run_bb(busybox, ["vi", "-H"], cwd=work).returncode != 0
    assert run_bb(busybox, ["vi", "-h"], cwd=work).returncode != 0
    vi_ro = work / "vi_ro.txt"
    vi_ro.write_text("ro_line\n", encoding="utf-8")
    vi_ro.chmod(0o444)
    _run_vi_piped(
        ["vi", "-R", "vi_ro.txt"],
        (b"\x1b[24;80RiX\x1b", b"ZZ:w\r:w!\r:w! vi_ro_out.txt\r:q!\r"),
    )
    vi_ro.chmod(0o644)
    # No filename buffer: test :w, :e, :r, :!%, :!#, empty buffer deletion, undo queue overflow
    no_fn_chunks: tuple[bytes, ...] = (
        b"\x1b[24;80R"
        b":s//x/\r"
        b"n"
        b":w\r"
        b":w vi_f2.txt\r"  # fn == NULL && stat(exp) == 0 -> File exists (:w! overrides)
        b":e\r"
        b":r\r"
        b":e %\r"
        b":e #\r" + b"i" + (b"a" * 270) + (b"\x08" * 265) + b"b\x08\x1b",
        b"uu" + b"i" + (b"x" * 140) + b"\x1b",
        b":%d\r:f vi_named.txt\r:wq\r",
    )
    _run_vi_piped(["vi"], no_fn_chunks)
    subprocess.run(
        _bb_cmd(busybox, ["vi", "vi_f2.txt"]),
        cwd=work,
        env={**os.environ, "LINES": "24", "COLUMNS": "80"},
        input=b"\x1b[24;80RZQ",
        capture_output=True,
        check=True,
    )

    # Test EXINIT and HOME/.exrc
    subprocess.run(
        _bb_cmd(busybox, ["vi", "-c", "q!", "vi_f2.txt"]),
        cwd=work,
        env={**os.environ, "EXINIT": "set ai\n\nset ic"},
        input=b"\x1b[24;80R",
        capture_output=True,
        check=True,
    )
    exrc_home = work / "exrc_home"
    exrc_home.mkdir(exist_ok=True)
    exrc_f = exrc_home / ".exrc"
    exrc_f.write_text("set ic\n\nset ai\n", encoding="utf-8")
    exrc_f.chmod(0o600)
    env_no_exinit = {k: v for k, v in os.environ.items() if k != "EXINIT"}
    env_no_exinit["HOME"] = str(exrc_home)
    subprocess.run(
        _bb_cmd(busybox, ["vi", "-c", "q!", "vi_f2.txt"]),
        cwd=work,
        env=env_no_exinit,
        input=b"\x1b[24;80R",
        capture_output=True,
        check=True,
    )
    exrc_f.chmod(0o666)
    subprocess.run(
        _bb_cmd(busybox, ["vi", "-c", "q!", "vi_f2.txt"]),
        cwd=work,
        env=env_no_exinit,
        input=b"\x1b[24;80R",
        capture_output=True,
        check=True,
    )
    subprocess.run(
        _bb_cmd(busybox, ["vi", "-c", "q!", "vi_f2.txt"]),
        cwd=work,
        env={k: v for k, v in env_no_exinit.items() if k != "HOME"},
        input=b"\x1b[24;80R",
        capture_output=True,
        check=True,
    )
    subprocess.run(
        _bb_cmd(busybox, ["vi", "-c", "q!", "vi_f2.txt"]),
        cwd=work,
        env={**env_no_exinit, "HOME": ""},
        input=b"\x1b[24;80R",
        capture_output=True,
        check=True,
    )

    # --- Extended awk.c coverage ---
    awk_prog_f = work / "prog.awk"
    awk_prog_f.write_text(
        "# comment line\n"
        "\n"
        "function myfunc(a, b, loc_arr) {\n"
        "  ;\n"
        "  loc_arr[1]=a+b;\n"
        "  for (k in loc_arr) { for (k in loc_arr) { } }\n"
        "  return loc_arr[1];\n"
        "}\n"
        "func emptyfunc(a, loc_a, w)\n"
        "{\n"
        "  loc_a[1] = a;\n"
        "  for (w in loc_a) { for (w in loc_a) { return; } }\n"
        "};\n"
        "BEGIN {\n"
        "  x = 0x10 + 010 + 009.5 + 000.5 + .5;\n"
        '  vconcat="abc"; vres=vconcat.2;\n'
        '  s = "esc\\"\\z\\n\\\ncont";\n'
        "  print myfunc(1, 2), emptyfunc(10, 20, 30, 888);\n"
        "}\n",
        encoding="utf-8",
    )
    awk_in1 = work / "awk_in1.txt"
    awk_in2 = work / "awk_in2.txt"
    awk_out1 = work / "awk_out1.txt"
    awk_in1.write_text(f"a b c{NL}d e f{NL}g h i{NL}", encoding="utf-8")
    awk_in2.write_text(f"1 2 3{NL}4 5 6{NL}", encoding="utf-8")
    assert_ok(
        run_bb(
            busybox,
            [
                "awk",
                "-F",
                "\\t\\",
                "-v",
                "VAR1=val\\n1",
                "-W",
                "anything",
                "-f",
                str(awk_prog_f),
                "-e",
                "BEGIN { for (i = 0; i < 600; i++) big[i] = i; delete big[1]; delete big }",
            ],
        ),
        "3",
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", "-E", str(awk_prog_f), "VAR2=ignored_as_file"],
            stdin_data=b"hello\n",
        ),
        "3",
    )
    awk_e_file = work / "awk_e_prog.awk"
    awk_e_file.write_text("{ print $1 }\n", encoding="utf-8")
    assert_ok(
        run_bb(
            busybox,
            ["awk", "-E", str(awk_e_file), str(awk_in1)],
        ),
        "a",
    )
    awk_comprehensive = (
        'function f_arr(arr) { arr["k"] = 42; delete arr["k"]; delete arr }\n'
        "function f_empty() { }\n"
        "BEGIN {\n"
        '  CONVFMT = "%.2f"; a = 1.234; b = a "";\n'
        '  CONVFMT = 9; c = 2.345; d = c "";\n'
        '  OFMT = "%d%%"; print 1.5;\n'
        '  OFMT = "%%prefix%d%"; print 1.5;\n'
        '  OFMT = "no_spec"; print 1.5;\n'
        '  OFMT = "unterm%"; print 1.5;\n'
        '  OFMT = "%s"; print 1.5;\n'
        '  OFMT = "%.2f"; print 1.5, 2;\n'
        '  printf "plain_no_fmt\\n";\n'
        '  printf "%d\\n";\n'
        '  printf "100%%_done_%W_%*W_%d%%_end\\n", 7;\n'
        '  printf "pre_%+- 0#\'*.*f_%10.2f_%10f_%*c_%c_%*s_%u_%u_%x_%o_mid%f\\n", '
        '6, 2, 3.14159, 1.5, 2.5, 2, "A", 0, -4, "hi", -1.9, 5, 255, 8, 1.25;\n'
        '  printf "unterm_after_%d_%", 1;\n'
        "  print atan2(1, 1), cos(0), exp(1), int(3.9), log(1), "
        "rand(), sin(0), sqrt(4), srand(), srand(42);\n"
        "  print and(3, 1), or(2, 1), xor(3, 1), compl(1), lshift(1, 2), rshift(8, 1), compl(-1);\n"
        '  print tolower("AbC!"), toupper("aBc!"), length(), '
        'length("abc"), length (1), f_empty();\n'
        "  arr_len[1] = 1; arr_len[2] = 2; print length(arr_len), length;\n"
        '  print index("hello", "ll"), index("hello", ""), '
        'index("hi", "longer"), index("hello", "xyz");\n'
        "  IGNORECASE = 1;\n"
        '  print index("Hello", "LL"), index("Hello", "xyz"), ("a" == "A");\n'
        "  IGNORECASE = 0;\n"
        '  print substr("abcdef", 2, 3), substr("abcdef", 0), substr("abcdef", 99, -1);\n'
        '  print match("abc123def", /[0-9]+/), match("abc", "[0-9]+"), RSTART, RLENGTH;\n'
        '  print split("a,b,c", sp_a, ","), split("a--b--", sp_b, /--/), '
        'split("ab", sp_c, ""), split("  x \\t y \\n ", sp_d, " ");\n'
        '  $0 = "a b";\n'
        '  print split("", sp_empty1, /--/), split("", sp_empty2, ","), split($0, sp_def);\n'
        '  print split("a::b", sp_re1, "::"), split("a::b", sp_re2, ":+");\n'
        '  IGNORECASE = 1; print split("aXbxc", sp_e, "x"); IGNORECASE = 0;\n'
        '  v_sub = "aba"; print sub(/\\n\\\\\\z\\//, "x", v_sub), '
        'sub(/a/, "<&>\\\\a\\\\\\\\\\\\", v_sub), v_sub;\n'
        '  $0 = "hello_world"; print split($0, sp_f0), sub(/world/, "awk"), gsub(/l/, "L"), $0;\n'
        '  v_gsub = "aba"; print gsub("a*", "X", v_gsub), v_gsub;\n'
        '  print gensub(/(a)(b)/, "\\\\2-\\\\1-\\\\z-&-\\\\0-\\\\", "g", "ab_ab");\n'
        '  print gensub(/a/, "X", 2, "aaa");\n'
        '  print systime(), strftime(), strftime("%Y", 0);\n'
        '  print mktime("2020 01 01 00 00 00"), mktime("bad"), '
        'mktime("2020 0 1 0 0 0"), mktime("1800 1 1 0 0 0");\n'
        '  print sprintf("%s=%d", "k", 10);\n'
        '  u = 1; print ++u, u++, --u, u--, +u, -u, !u, !0, !"", !"x";\n'
        "  u += 2; u -= 1; u *= 3; u /= 2; u %= 2; u ^= 3; u **= 1;\n"
        "  print (1 + 2 - 1 * 3 / 1 % 2 ^ 2 ** 1);\n"
        "  print (1 < 2), (1 <= 2), (1 > 2), (1 >= 2), (1 == 1), (1 != 2), "
        '("a" < "b"), (1 < "b");\n'
        '  print ("abc" ~ /b/), ("abc" !~ /b/), ("abc" ~ "b"), (0 && 1), '
        "(1 && 1), (1 && 0), (0 || 1), (1 || 0), (1 ? 10 : 20), (0 ? 10 : 20);\n"
        '  m_arr[1, 2] = "multi"; print ((1, 2) in m_arr), ("none" in m_arr);\n'
        "  f_arr(m_arr);\n"
        "  i = 0; while (i < 3) { i++; if (i == 1) continue; if (i == 2) break; }\n"
        "  do { i++; } while (i < 4);\n"
        "  for (j = 0; j < 2; j++) { if (j == 0) ; else ; }\n"
        "  for (;;) { break; }\n"
        "  NF = 0;\n"
        '  fflush(); fflush(""); fflush("/dev/stdout");\n'
        '  print close("never_opened");\n'
        f'  print "out_line" > "{awk_out1}";\n'
        f'  print "app_line" >> "{awk_out1}";\n'
        f'  print close("{awk_out1}");\n'
        f'  while ((getline gl_v < "{awk_out1}") > 0) print gl_v;\n'
        f'  close("{awk_out1}");\n'
        '  print (getline < "/nonexistent_awk_file_xyz");\n'
        '  close("/nonexistent_awk_file_xyz");\n'
        '  "echo pipe_in" | getline p_v; close("echo pipe_in"); print p_v;\n'
        '  print "pipe_out" | "cat >/dev/null";\n'
        '  print system(""), system("true");\n'
        "}\n"
    )
    assert_ok(run_bb(busybox, ["awk", awk_comprehensive]))
    # Main loop, range patterns, FS/RS/NF/$N, next, nextfile, END, and ARGV assignments
    awk_stream_prog = (
        'BEGIN { FS = " *"; RS = "\\n"; }\n'
        '/a/,/d/ { $2 = "MOD"; $20 = "FAR"; NF = 2; $0 = $0; print; }\n'
        "/g/ { next }\n"
        "NR == 1 { next }\nNR == 2 { getline; nextfile }\n"
        "/1/;\n"
        "{ print /2/ }\n"
        'END { print "done_end" }\n'
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "awk",
                "-v",
                "U_NUM=123 ",
                "-v",
                "U_STR=123abc",
                "-v",
                "U_EMPTY=",
                awk_stream_prog + "BEGIN { print U_NUM + 1, U_STR + 1, U_EMPTY + 1 }\n",
                "VAR_ASSIGN=123",
                str(awk_in1),
                str(awk_in2),
            ],
        )
    )
    # Paragraph mode RS="" (>256 bytes), regex RS, and NUL-terminated input without trailing NL
    assert_ok(
        run_bb(
            busybox,
            ["awk", 'BEGIN { RS = ""; FS = ":+" } { print $1, $2 }'],
            stdin_data=b"\n\n" + (b"a" * 300) + b":b\nc:d\n\n\ne:f",
        ),
        "b",
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", 'BEGIN { RS = "SEP+"; FS = " *" } { print $1, $2 }'],
            stdin_data=b"x ySEPrec2SEP",
        ),
        "x y",
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", "{ print $1 }"],
            stdin_data=b"no_newline\x00after_nul",
        ),
        "no_newline",
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", "{ getline; print $0 }"],
            stdin_data=b"s1\ns2\n",
        ),
        "s2",
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", "BEGIN { getline; print $0 } { print $0 }"],
            stdin_data=b"b1\nb2\n",
        ),
        "b1",
    )
    # Exit inside END block (re-entrant awk_exit) and BEGIN+END without main block
    assert_ok(
        run_bb(
            busybox,
            ["awk", "BEGIN { x = 1 } END { exit 0 }"],
            stdin_data=b"",
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", "BEGIN { exit 0 }"],
            stdin_data=b"",
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["awk", "BEGIN { x = 1 } END { print x }"],
            stdin_data=b"",
        ),
        "1",
    )
    # Unclosed regex fallback (~REG_EXTENDED) and empty ARGV slot with main block
    assert_ok(
        run_bb(
            busybox,
            [
                "awk",
                'BEGIN { ARGV[1] = ""; s = "a{1,2"; gsub("a{1,2", "X", s); print s } { print $1 }',
                str(awk_in1),
                str(awk_in2),
            ],
        ),
        "X",
    )
    # Error / syntax_error paths in awk
    for bad_awk in (
        ["awk"],
        ["awk", "-v", "bad_assign", "BEGIN {}"],
        ["awk", "-Z"],
        ["awk", 'BEGIN { print "unterm\n" }'],
        ["awk", 'BEGIN { print "unterm\\\n'],
        ["awk", "BEGIN { print /unterm\n }"],
        ["awk", "BEGIN { x = 1.2.3 }"],
        ["awk", "BEGIN { @ }"],
        ["awk", "BEGIN { + }"],
        ["awk", "BEGIN { : }"],
        ["awk", "BEGIN { 1 = 2 }"],
        ["awk", "BEGIN { () }"],
        ["awk", "BEGIN { for (x) print x }"],
        ["awk", "BEGIN { break }"],
        ["awk", "BEGIN { continue }"],
        ["awk", "BEGIN { delete }"],
        ["awk", "BEGIN\n"],
        ["awk", "BEGIN { print 1 2 = 3 }"],
        ["awk", "function f() {} function f() {}"],
        ["awk", "function f(a,) {}"],
        ["awk", "BEGIN { undef_fn() }"],
        ["awk", "BEGIN { print 1 ? 2 }"],
        ["awk", "BEGIN { delete 1 }"],
        ["awk", 'BEGIN { OFMT = "%d %d"; print 1.5 }'],
        ["awk", 'BEGIN { OFMT = "%*f"; print 1.5 }'],
        ["awk", 'BEGIN { OFMT = "%z"; print 1.5 }'],
        ["awk", 'BEGIN { OFMT = "%d%d"; print 1.5 }'],
        ["awk", "BEGIN { NF = -1 }"],
        ["awk", "BEGIN { print $(-1) }"],
        ["awk", "BEGIN { print 1 / 0 }"],
        ["awk", "BEGIN { print 1 % 0 }"],
        ["awk", "BEGIN { print cos() }"],
        ["awk", "BEGIN { print rand(1) }"],
        ["awk", "BEGIN { print systime(1) }"],
        ["awk", "BEGIN { print index(1) }"],
    ):
        assert run_bb(busybox, bad_awk, stdin_data=b"x\n").returncode != 0

    assert_ok(
        run_bb(busybox, ["bc"], stdin_data=f"6 * 7{NL}quit{NL}".encode()),
        "42",
    )
    assert_ok(run_bb(busybox, ["dc", "-e", "6 7 * p"]), "42")
    assert_ok(run_bb(busybox, ["bc", "-v"]))
    for bclen in ("0", "1", "10", "abc", "99999999999999999999"):
        bc_env = os.environ.copy()
        bc_env["BC_ENV_ARGS"] = " -w -q "
        bc_env["POSIXLY_CORRECT"] = "1"
        bc_env["BC_LINE_LENGTH"] = bclen
        assert_ok(
            subprocess.run(
                _bb_cmd(busybox, ["bc"]),
                input=b"1 + 1\nquit\n",
                env=bc_env,
                capture_output=True,
                check=False,
            )
        )
    dc_env = os.environ.copy()
    dc_env["BC_ENV_ARGS"] = "-q"
    dc_env["DC_LINE_LENGTH"] = "1"
    assert_ok(
        subprocess.run(
            _bb_cmd(busybox, ["bc"]),
            input=b"1 + 1\nquit\n",
            env=dc_env,
            capture_output=True,
            check=False,
        )
    )
    assert_ok(
        subprocess.run(
            _bb_cmd(busybox, ["dc", "-e", "1 1 + p"]),
            env=dc_env,
            capture_output=True,
            check=False,
        )
    )

    bc_math_script = (
        "scale=10\n"
        "e(0); e(-2); e(2); e(0.5)\n"
        "l(1); l(0); l(-1); l(4); l(0.1); l(0.8)\n"
        "s(0); s(-1); s(1); s(4); s(10)\n"
        "c(0); c(1); c(-1)\n"
        "a(0); a(1); a(-1); a(0.2); a(-0.2); a(0.5); a(-0.5); a(2)\n"
        "scale=70\n"
        "a(1); a(0.2); a(0.1)\n"
        "scale=10\n"
        "j(0, 1); j(2, 1.5); j(-1, 1.5); j(-2, 1.5)\n"
        "quit\n"
    )
    assert_ok(run_bb(busybox, ["bc", "-l"], stdin_data=bc_math_script.encode()))

    dc_file = work / "script.dc"
    dc_file.write_text("10 20 + p\n", encoding="utf-8")
    assert_ok(
        run_bb(
            busybox,
            ["dc", "-x", "-e", "2 3 * p", "-f", str(dc_file), str(dc_file)],
        )
    )
    dc_script = (
        "# dc comment\n"
        "10 3 ~ f c\n"
        "2 10 100 | p c\n"
        "1 2 3 z p c\n"
        "65 a d P a P [hello] a P [stream_str] P 257 P 0 P c\n"
        "16 i FF p 10 i\n"
        "16 o 255 p 2 o 7 p 100 o 12345 p 0 p _123 p 10 o\n"
        "2 k K p 0.01 0.01 - Z p X p 0 Z p 0.005 Z p\n"
        "[hello] Z p [hello] sZ lZ Z p\n"
        "5 3 - p 3 5 - p 5 3 * p 5 3 / p 5 3 % p 2 8 ^ p 9 v p\n"
        "_5 p .5 p\n"
        "5 d p 3 7 r p\n"
        "42 sA lA d p p\n"
        "10 SB 20 SB LB p LB p\n"
        "[reg_str] SC LC d a p P\n"
        "99 5 :a 5 ;a p\n"
        "1 2 ( p 2 1 ( p 2 2 ( p\n"
        "1 2 { p 2 2 { p 3 2 { p\n"
        "2 2 G p 2 3 G p\n"
        "0 N p 5 N p\n"
        "[100 p] sM lM x [200 p] x\n"
        "1 2 <M 2 1 <M\n"
        "2 1 >M 1 2 >M\n"
        "2 2 =M 2 3 =M\n"
        "1 2 !<M 2 1 !>M 2 3 !=M\n"
        "[10 p] sT [20 p] sF\n"
        "2 1 >TeF 1 2 >TeF\n"
        "[10 p] s _reg 1 2 < _reg\n"
        "123 n [str_n] n\n"
        "[[]] p [] a p\n"
        "5 x\n"
        "[[[q] x 99 p] x 88 p] x\n"
        "[[[100 p] x 2 Q] x] x\n"
        "[[1 Q] x] x\n"
        "[q] x\n"
    )
    assert_ok(run_bb(busybox, ["dc", "-x"], stdin_data=dc_script.encode()))
    dc_q_file = work / "dc_q.dc"
    dc_q_file.write_text("? p\n", encoding="utf-8")
    assert_ok(run_bb(busybox, ["dc", str(dc_q_file)], stdin_data=b"5 5 +\n"), "10")
    assert_ok(run_bb(busybox, ["dc"], stdin_data=b"1 Q\n"))

    big_bc_expr = " + ".join(str(i) for i in range(1, 265)) + "\nquit\n"
    assert_ok(run_bb(busybox, ["bc"], stdin_data=big_bc_expr.encode()))

    bc_script = (
        "/* multiline\n"
        "   comment */\n"
        "/* * / ** */\n"
        "# hash comment\n"
        "ibase = 16; obase = 16; scale = 4\n"
        "FF + 1\n"
        "ibase = A; obase = A\n"
        "a = 5; ++a; a++; --a; a--; -a; !a; !0\n"
        "a ^= 2; a *= 2; a /= 3; a %= 5; a += 10; a -= 2\n"
        "2 ^ -1; 2 ^ -3\n"
        "1 == 1; 1 <= 2; 2 >= 1; 1 != 2; 1 < 2; 2 > 1\n"
        "if (1 < 2 < 3) 1\n"
        "1 && 2; 1 && 0; 0 && 1; 1 || 0; 0 || 1; 0 || 0\n"
        "arr[0] = 10; arr[1] = 20; length(arr[]); scale(1.23); sqrt(16)\n"
        "length(0); length(100); length(0.01 - 0.01); length(0.000123)\n"
        'print "str_lit\\n\\a\\b\\f\\r\\t\\e\\\\\\q\\", 123, "\\n"\n'
        '"no_nl_str"; "multi\n'
        "line\n"
        'str"\n'
        "define void vfn(x, *y[])\n"
        "{\n"
        "    auto loc, larr[];\n"
        "    loc = x + 1\n"
        "    larr[0] = y[0]\n"
        "    { loc = loc + 1 }\n"
        "    return\n"
        "}\n"
        "define void vfn_semi() { return; }\n"
        "define fn3(z[]) {\n"
        "    return z[0];\n"
        "}\n"
        "define fn2(x, *y[]) {\n"
        "    if (x == 0) return (10);\n"
        "    if (x == 1) return 20;\n"
        "    if (x == 2) return fn3(y[]);\n"
        "    if (x == 3) return (1) + (2);\n"
        "    if (x == 4) { return }\n"
        "    if (x == 5) return 1 + (2);\n"
        "    return (y[0] + x);\n"
        "}\n"
        "vfn(5, arr[]); vfn_semi()\n"
        "fn2(0, arr[]); fn2(1, arr[]); fn2(2, arr[]); "
        "fn2(3, arr[]); fn2(4, arr[]); fn2(5, arr[]); fn2(6, arr[])\n"
        "i = 0; while (i < 3) { i += 1; if (i == 1) continue; if (i == 2) break; }\n"
        "for (i = 0; i < 3; i++) { if (i == 1) continue; if (i == 2) break; }\n"
        "for (; i < 5; ) { i++; break; }\n"
        "if (1) 10 else 20\n"
        "if (0) 10 else 20\n"
        "limits\n"
        "last + 1\n"
        "read()\n"
        "999\n"
        "halt\n"
    )
    assert_ok(run_bb(busybox, ["bc", "-w"], stdin_data=bc_script.encode()))

    fn_redef = "\n".join(f"define f{i:02d}(x) {{ return x; }}" for i in range(40, 0, -1))
    fn_redef += (
        "\ndefine f01(x) { auto y; return x; }\ndefine f01(x) { return x+1; }\nf01(1)\nquit\n"
    )
    assert_ok(run_bb(busybox, ["bc"], stdin_data=fn_redef.encode()))

    bc_more = (
        "scale = 4\n"
        "sqrt(0); sqrt(1); sqrt(100); sqrt(1000); sqrt(0.04)\n"
        "0 ^ 0; 0 ^ 2; 2 ^ 0; 2 ^ 3; 2 ^ -2; 0.5 ^ 2\n"
        "scale = 0; 2 ^ -2; 0.5 ^ 2; 1.5 ^ 2; 1.5 ^ -2; scale = 4\n"
        "0 / 5; 0 % 5; 5 * 0; 5 / 1; 0 + 5; 5 + 0; 0 - 5; 5 - 0; 5 - 5; 0.01 - 0.01\n"
        "0 + 0; 0 - 0; -5 + 0; 0 + -5; -5 - 0; 0 - -5\n"
        "-1 + 5; 1 - 5\n"
        "1.5 * 2; 2 * 1.5; 5 / 1.5\n"
        "1 / 0.25; 100 / 3; 1 / 0.0025; 1 % 10; 10 % 3\n"
        "scale = 2; 0.1 / 1; 0.1 % 1; scale = 4\n"
        "-5 < -10; -10 < -5; -5 < 5; 5 < -5\n"
        "1.2300 == 1.23; 1.2301 > 1.23; 1.23 < 1.2301\n"
        ".5 + .5\n"
        ". + 1\n"
        "123\\\n"
        "456\n"
        "123.\n"
        "0.000\n"
        "A; Z; ZZ\n"
        "ibase = 16; 0; 0.0; 12.34; .5; Z.Z; 0.1; 10; 0.8; ibase = A\n"
        "obase = 16; 0; -15.5; obase = 100; 123456.789; -123; 0; obase = 10\n"
        "10 ^ 80\n"
        "obase = 16; 16 ^ 80; obase = 100; 10 ^ 80; obase = 10\n"
        "++ibase; --ibase; ibase++; ibase--\n"
        "++obase; --obase; obase++; obase--\n"
        "scale = 4; ++scale; --scale; scale++; scale--\n"
        "10; ++. ; --. ; .++ ; .--\n"
        "!1; !!1; !1 + 2; (-1) + 2; (!1); (-1)\n"
        "a = 5; a++ - 1; (1) - 1; -(1)\n"
        "arr[0] = 1; ++arr[0]; arr[0]++; . = 5\n"
        "ifz = 1; if_ = 2; long_name = 3\n"
        "for (; ; ) { break; }\n"
        "define ref_val(v[]) { return v[0]; }\n"
        "define ref_fn2(*r[]) { return ref_val(r[]); }\n"
        "define ref_fn1(*r[]) {\n"
        "    auto larr[];\n"
        "    larr[0] = r[0];\n"
        "    return ref_fn2(r[]);\n"
        "}\n"
        "arr[0] = 77\n"
        "ref_fn1(arr[])\n"
        "ref_val(arr[])\n"
        "quit\n"
    )
    assert_ok(run_bb(busybox, ["bc", "-w"], stdin_data=bc_more.encode()))
    assert_ok(run_bb(busybox, ["bc"], stdin_data=b"123"), "123")
    assert_ok(run_bb(busybox, ["bc"], stdin_data=b"1 + \\\n2\nquit\n"), "3")
    assert_ok(
        run_bb(
            busybox,
            ["bc"],
            stdin_data=(
                b"define f(a) { auto b[]; ; }\n"
                b"for (i = 0; i < 2; i++) { continue; }\n"
                b"define void vf() { return; }\n"
                b"vf() + 1; -vf(); a = vf()\n"
                b"quit\n"
            ),
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["dc", "-x"],
            stdin_data=b"lZ p [str] Sa [str] sA [a\nb] p\n",
        )
    )

    bad_bc_f = work / "bad.bc"
    bad_bc_f.write_bytes(b"\x00\x01\n")
    assert run_bb(busybox, ["bc", str(bad_bc_f)]).returncode != 0
    err_bc_f = work / "err.bc"
    err_bc_f.write_text("1 / 0\n", encoding="utf-8")
    assert run_bb(busybox, ["bc", str(err_bc_f)]).returncode != 0
    assert run_bb(busybox, ["dc", "-e", "1 0 / p"]).returncode != 0
    assert run_bb(busybox, ["dc", "-f", str(err_bc_f)]).returncode != 0
    assert run_bb(busybox, ["dc", "-z"]).returncode != 0
    assert run_bb(busybox, ["bc", "-s"], stdin_data=b"# comment\n").returncode != 0
    for bad_bc_in in (
        b"\x01\n",
        b"\x7f\n",
        b"@\n",
        b"& 1\n",
        b"| 1\n",
        b"\\ 1\n",
        b"1.2.3\n",
        b'"unterm',
        b"/* unterm",
        b"define 1\n",
        b"define f 1\n",
        b"define f() 1\n",
        b"define f(1)\n",
        b"define f(a,)\n",
        b"define f(a[1])\n",
        b"define f(*a)\n",
        b"define f(a, a) { return a; }\n",
        b"define f(a) { auto a; return a; }\n",
        b"define f(a) { auto 1; }\n",
        b"define f(a) { auto b[1]; }\n",
        b"define f(a) { auto b c; }\n",
        b"{ print 1 print 2 }\n",
        b"{ 1 2 }\n",
        b"else\n",
        b"1 + * 2\n",
        b"1 = 2\n",
        b"++a++\n",
        b"1 !\n",
        b"! + 1\n",
        b"1 (2)\n",
        b"1 a\n",
        b"1 2\n",
        b"1 ibase\n",
        b"1 length(1)\n",
        b"1 read()\n",
        b"1 scale\n",
        b"1 / 0\n",
        b"1 % 0\n",
        b"2 ^ 1.5\n",
        b"sqrt(-1)\n",
        b"ibase = 1\n",
        b"ibase = 99\n",
        b"obase = 1\n",
        b"obase = 999999\n",
        b"scale = -1\n",
        b"scale = 5000000000\n",
        b"arr[-1]\n",
        b"arr[3000000000]\n",
        b"2 ^ 99999999999999999999999999\n",
        b"undef_fn()\n",
        b"define f(a, b) { return a; }\nf(1)\n",
        b"define f(a[]) { return 1; }\nf(1)\n",
        b"define f(a) { return 1; }\nf(arr[])\n",
        b"break\n",
        b"return\n",
        b"if 1\n",
        b"if (1\n",
        b"if (1)\n\n",
        b"while 1\n",
        b"while (1\n",
        b"for 1\n",
        b"for (i=0)\n",
        b"for (i=0; i<1)\n",
        b"for (i=0; i<1; i++ 1\n",
        b"for (i=0; i<1; i++]\n",
        b"length 1\n",
        b"length(1\n",
        b"scale(1\n",
        b"++scale(1)\n",
        b"++1\n",
        b"++f()\n",
        b"read + 1\n",
        b"read(1\n",
        b"read(\n",
        b"read()\n1; 2\n",
        b"a[\n",
        b"a[]\n",
        b"a[1,2]\n",
        b"f(1,)\n",
        b"f(1]\n",
        b"f(1 2)\n",
        b"()\n",
        b"1 )\n",
        b"1 + (\n",
        b"a = 1; a() \n",
    ):
        assert run_bb(busybox, ["bc"], stdin_data=bad_bc_in).returncode != 0
    for bad_dc_in in (
        b"p\n",
        b"Z\n",
        b"[str] 1 + p\n",
        b"1 [str] + p\n",
        b"[str] v\n",
        b"[str] _1 * p\n",
        b"[str] N\n",
        b"1 2 |\n",
        b"[s] 1 2 |\n",
        b"1 2 0 |\n",
        b"_5 P\n",
        b"2 _1 5 |\n",
        b"1.5 2 3 |\n",
        b"a\n",
        b"P\n",
        b"10 Q\n",
        b"_1 Q\n",
        b"Q\n",
        b"x\n",
        b"10 sN 1 <N\n",
        b"10 sN 2 1 <NeN\n",
        b"[1 +] x\n",
        b"d\n",
        b"r\n",
        b"1 r\n",
        b"LZ\n",
        b"s\n",
        b"S\n",
        b"s+\n",
        b"[str] 0 :a\n",
        b"[str] i\n",
        b"0 :a\n",
        b";a\n",
        b"!\n",
        b"\\\n",
        b".\n",
        b"_a\n",
        b"[unterm",
        b'"bad\n',
    ):
        assert run_bb(busybox, ["dc", "-x"], stdin_data=bad_dc_in).returncode != 0

    assert_ok(
        run_bb(busybox, ["find", str(work), "-type", "f", "-print"]),
        "ed_doc.txt",
    )
    find_dir = work / "find_dir"
    (find_dir / "sub").mkdir(parents=True)
    (find_dir / "sub" / "leaf.txt").write_text("x", encoding="utf-8")
    (find_dir / "-").write_text("dash", encoding="utf-8")
    (find_dir / "!not_opt").write_text("bang", encoding="utf-8")
    (find_dir / "(not_opt").write_text("paren", encoding="utf-8")
    for f_args in (
        ["find"],
        ["find", "-maxdepth", "0"],
        ["find", "-H", "-L", "-P", "--", str(find_dir), "-mindepth", "1", "-maxdepth", "1"],
        ["find", "-", "-maxdepth", "0"],
        ["find", "-follow", "-depth", str(find_dir), "-maxdepth", "1"],
        ["find", str(find_dir), "-name", "leaf.txt"],
        [
            "find",
            str(find_dir),
            "-iname",
            "LEAF.TXT",
            "-a",
            "-path",
            "*sub*",
            "-and",
            "-wholename",
            "*leaf*",
        ],
        [
            "find",
            str(find_dir),
            "-ipath",
            "*LEAF*",
            "-o",
            "!",
            "-name",
            "none",
            "-or",
            "-not",
            "-name",
            "none",
        ],
        ["find", str(find_dir), "-maxdepth", "0", "-print0"],
        [
            "find",
            str(find_dir),
            "(",
            "(",
            "-name",
            "(foo",
            "-o",
            "-name",
            ")bar",
            "-o",
            "-name",
            "leaf.txt",
            ")",
            ")",
        ],
        ["find", "!", "-maxdepth", "0"],
        ["find", "(", "-maxdepth", "0", ")"],
        ["find", "!not_opt", "(not_opt", "-maxdepth", "0"],
        [
            "find",
            str(find_dir),
            "-type",
            "b",
            "-o",
            "-type",
            "c",
            "-o",
            "-type",
            "d",
            "-o",
            "-type",
            "p",
            "-o",
            "-type",
            "f",
            "-o",
            "-type",
            "l",
            "-o",
            "-type",
            "s",
        ],
    ):
        run_bb(busybox, f_args, cwd=find_dir)
    for bad_find in (
        ["find", str(find_dir), "-type", "z"],
        ["find", str(find_dir), "-type", "ff"],
        ["find", str(find_dir), "-name"],
        ["find", str(find_dir), "(", "-name", "x"],
        ["find", "--unknown_opt"],
        ["find", str(find_dir), "-unknown_opt"],
        ["find", str(work / "missing_find_path")],
    ):
        assert run_bb(busybox, bad_find, cwd=work).returncode != 0

    assert_ok(
        run_bb(busybox, ["xargs", "echo"], stdin_data=f"  x1   x2 x3  {NL}".encode()),
        "x1 x2 x3",
    )
    assert_ok(run_bb(busybox, ["xargs", "echo"], stdin_data=b""))
    assert_ok(
        run_bb(
            busybox,
            ["xargs"],
            stdin_data=b"\\a 'single' \"double\" trailing",
        ),
        "a single double trailing",
    )
    assert_ok(
        run_bb(
            busybox,
            ["xargs", "-r", "-t", "-o", "-n", "1", "-s", "200", "echo"],
            stdin_data=f"one{NL}two{NL}".encode(),
        ),
        "one",
    )
    assert_ok(
        run_bb(
            busybox,
            ["xargs", "-E", "STOP", "echo"],
            stdin_data=f"one STOP two{NL}".encode(),
        ),
        "one",
    )
    assert_ok(run_bb(busybox, ["xargs", "-r", "-E", "", "echo"], stdin_data=b""))
    assert_ok(
        run_bb(busybox, ["xargs", "-0", "-n", "1", "echo"], stdin_data=b"z1\x00z2"),
        "z1",
    )
    assert_ok(
        run_bb(busybox, ["xargs", "-0", "-n", "2", "echo"], stdin_data=b"z1\x00z2"),
        "z1 z2",
    )
    assert_ok(
        run_bb(
            busybox,
            ["xargs", "-p", "-n", "1", "echo"],
            stdin_data=f"p1{NL}y{NL}p2{NL}n{NL}".encode(),
        ),
        "p1",
    )
    for bad_xargs, bad_stdin in (
        (["xargs", "echo"], b"'unmatched"),
        (["xargs", "echo"], b'"unmatched'),
        (["xargs", "-s", "10", "echo"], b"1234567890"),
        (["xargs", "-0", "-s", "10", "echo"], b"1234567890"),
        (["xargs", "-s", "2", "echo"], b"a"),
        (["xargs", "/bin/sh", "-c", "exit 255"], f"a{NL}".encode()),
    ):
        assert run_bb(busybox, bad_xargs, stdin_data=bad_stdin).returncode != 0
    assert (
        run_bb(
            busybox, ["xargs", "basename", "a", "b", "c"], stdin_data=f"d{NL}".encode()
        ).returncode
        != 0
    )
    dummy_bin = work / ("dummy_cmd.exe" if sys.platform == "win32" else "dummy_cmd")
    dummy_bin.write_bytes(f"#!/bin/sh{NL}".encode())
    dummy_bin.chmod(0o755)
    assert_ok(run_bb(busybox, ["which", str(dummy_bin)]), "dummy_cmd")
    subprocess.run(
        _bb_cmd(busybox, ["which", "dummy_cmd", "missing_cmd_xyz"]),
        env={"PATH": str(work)},
        capture_output=True,
        check=False,
    )
    subprocess.run(
        _bb_cmd(busybox, ["which", "-a", "dummy_cmd", str(work / "no_slash_bin")]),
        env={"PATH": f":{work}::{work}:"},
        cwd=work,
        capture_output=True,
        check=False,
    )
    subprocess.run(
        _bb_cmd(busybox, ["which", "missing_cmd_xyz"]),
        env={},
        capture_output=True,
        check=False,
    )


def run_archival_suite(busybox: Path, work: Path) -> None:
    """Verify archival and compression applets with real round-trip payloads.

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        work: Isolated temporary directory for test fixtures.
    """
    payload_str = f"BusyBox archival test payload 1234567890{NL}" * 4
    payload = payload_str.encode("utf-8")

    gz_file = work / "item.txt"
    gz_file.write_bytes(payload)
    assert_ok(run_bb(busybox, ["gzip", str(gz_file)], cwd=work))
    gz_archive = work / "item.txt.gz"
    assert gz_archive.exists()
    assert_ok(
        run_bb(busybox, ["zcat", str(gz_archive)], cwd=work),
        "BusyBox archival test payload",
    )
    gz_copy1 = work / "copy1.txt.gz"
    gz_copy2 = work / "copy2.txt.gz"
    gz_copy1.write_bytes(gz_archive.read_bytes())
    gz_copy2.write_bytes(gz_archive.read_bytes())
    assert_ok(run_bb(busybox, ["gunzip", str(gz_copy1), str(gz_copy2)], cwd=work))
    assert_ok(run_bb(busybox, ["gunzip", str(gz_archive)], cwd=work))
    assert gz_file.read_bytes() == payload

    bz_file = work / "item_bz.txt"
    bz_file.write_bytes(payload)
    assert_ok(run_bb(busybox, ["bzip2", str(bz_file)], cwd=work))
    bz_archive = work / "item_bz.txt.bz2"
    assert bz_archive.exists()
    assert_ok(
        run_bb(busybox, ["bzcat", str(bz_archive)], cwd=work),
        "BusyBox archival test payload",
    )
    assert_ok(run_bb(busybox, ["bunzip2", str(bz_archive)], cwd=work))
    assert bz_file.read_bytes() == payload
    assert_ok(run_bb(busybox, ["bzip2", "-1", "-c"], stdin_data=b"A" + b"B" * 300 + b"CC" + b"DDD"))
    assert_ok(
        run_bb(
            busybox,
            ["bzip2", "-c"],
            stdin_data=bytes(i % 251 for i in range(400)),
        )
    )
    bz_d_file = work / "bz_d_test.txt.bz2"
    bz_d_file.write_bytes(run_bb(busybox, ["bzip2", "-c"], stdin_data=payload).stdout)
    assert_ok(run_bb(busybox, ["bzip2", "-dt", str(bz_d_file)], cwd=work))

    lzo_file = work / "item_lzo.txt"
    lzo_file.write_bytes(payload)
    assert_ok(run_bb(busybox, ["lzop", str(lzo_file)], cwd=work))
    lzo_archive = work / "item_lzo.txt.lzo"
    assert lzo_archive.exists()
    assert_ok(
        run_bb(busybox, ["lzopcat", str(lzo_archive)], cwd=work),
        "BusyBox archival test payload",
    )
    lzo_file.unlink()
    assert_ok(run_bb(busybox, ["unlzop", str(lzo_archive)], cwd=work))
    assert lzo_file.read_bytes() == payload

    xz_archive = work / "item_xz.txt.xz"
    xz_archive.write_bytes(lzma.compress(payload, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32))
    assert_ok(
        run_bb(busybox, ["xzcat", str(xz_archive)], cwd=work),
        "BusyBox archival test payload",
    )
    assert_ok(
        run_bb(busybox, ["xz", "-dc", str(xz_archive)], cwd=work),
        "BusyBox archival test payload",
    )
    assert_ok(run_bb(busybox, ["unxz", str(xz_archive)], cwd=work))
    assert (work / "item_xz.txt").read_bytes() == payload
    assert run_bb(busybox, ["xz", "-dc"], stdin_data=b"corrupt_xz_stream").returncode != 0
    xz_rich_payload = (
        b"ABCD" * 5
        + b"XYZ"
        + b"ABCD" * 4
        + b"1234567890" * 3
        + b"Q" * 50
        + b"9876543210" * 2
        + b"ABCD" * 6
        + bytes(range(64)) * 3
    )
    assert_ok(
        run_bb(
            busybox,
            ["xz", "-dc"],
            stdin_data=lzma.compress(
                xz_rich_payload, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32
            ),
        )
    )

    zip_path = work / "archive.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("hello_zip.txt", f"unzip_verified_content{NL}")
    assert_ok(
        run_bb(busybox, ["unzip", "-l", str(zip_path)]),
        "hello_zip.txt",
    )
    unzip_dest = work / "unzip_out"
    assert_ok(run_bb(busybox, ["unzip", "-o", "-d", str(unzip_dest), str(zip_path)]))
    assert (unzip_dest / "hello_zip.txt").read_text(
        encoding="utf-8"
    ) == f"unzip_verified_content{NL}"
    assert_ok(
        run_bb(
            busybox,
            ["unzip", "-d", str(unzip_dest), str(zip_path)],
            stdin_data=f"r{NL}renamed_with_nl.txt{NL}".encode(),
        )
    )
    assert (unzip_dest / "renamed_with_nl.txt").exists()
    assert_ok(
        run_bb(
            busybox,
            ["unzip", "-d", str(unzip_dest), str(zip_path)],
            stdin_data=f"r{NL}renamed_no_nl.txt".encode(),
        )
    )
    assert (unzip_dest / "renamed_no_nl.txt").exists()

    cpio_src = work / "cpio_src"
    cpio_src.mkdir()
    (cpio_src / "cpio_item.txt").write_text(f"cpio_roundtrip_ok{NL}", encoding="utf-8")
    (cpio_src / "cpio_sub").mkdir()
    os.symlink("cpio_item.txt", cpio_src / "cpio_sym")
    cpio_res = run_bb(
        busybox,
        ["cpio", "-o", "-H", "newc"],
        cwd=cpio_src,
        stdin_data=f"cpio_item.txt{NL}cpio_sub{NL}cpio_sym{NL}".encode(),
    )
    assert cpio_res.returncode == 0
    assert_ok(
        run_bb(
            busybox,
            ["cpio", "-o", "-0", "-H", "newc"],
            cwd=cpio_src,
            stdin_data=b"cpio_item.txt\x00",
        )
    )
    cpio_dst = work / "cpio_dst"
    cpio_dst.mkdir()
    assert_ok(
        run_bb(
            busybox,
            ["cpio", "-i", "-u", "-d"],
            cwd=cpio_dst,
            stdin_data=cpio_res.stdout,
        )
    )
    assert (cpio_dst / "cpio_item.txt").read_text(encoding="utf-8") == f"cpio_roundtrip_ok{NL}"
    assert_ok(
        run_bb(
            busybox,
            ["cpio", "-it", "cpio_item.txt", "nonexistent_item.txt"],
            cwd=cpio_dst,
            stdin_data=cpio_res.stdout,
        ),
        "cpio_item.txt",
    )
    assert_ok(
        run_bb(
            busybox,
            ["cpio", "-it", "nonexistent_only.txt"],
            cwd=cpio_dst,
            stdin_data=cpio_res.stdout,
        )
    )
    tar_sub_src = work / "tar_sub_src"
    (tar_sub_src / "sub").mkdir(parents=True)
    (tar_sub_src / "sub" / "nested.txt").write_text(f"nested{NL}", encoding="utf-8")
    tar_sub_file = work / "sub_bundle.tar"
    assert_ok(
        run_bb(busybox, ["tar", "-cf", str(tar_sub_file), "-C", str(tar_sub_src), "sub/nested.txt"])
    )
    tar_sub_dst = work / "tar_sub_dst"
    tar_sub_dst.mkdir()
    assert_ok(run_bb(busybox, ["tar", "-xf", str(tar_sub_file), "-C", str(tar_sub_dst)]))

    tar_src = work / "tar_src"
    tar_src.mkdir()
    (tar_src / "tar_item.txt").write_text(f"tar_roundtrip_ok{NL}", encoding="utf-8")
    tar_file = work / "bundle.tar"
    assert_ok(
        run_bb(
            busybox,
            ["tar", "-cf", str(tar_file), "-C", str(tar_src), "tar_item.txt"],
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["tar", "-caf", str(work / "auto.tar.gz"), "-C", str(tar_src), "tar_item.txt"],
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["tar", "-caf", "x", "tar_item.txt"],
            cwd=tar_src,
        )
    )
    assert_ok(run_bb(busybox, ["tar", "-tf", str(tar_file)]), "tar_item.txt")
    assert_ok(
        run_bb(busybox, ["tar", "-xOf", str(tar_file)]),
        "tar_roundtrip_ok",
    )
    assert_ok(
        run_bb(busybox, ["tar", "-xvOf", str(tar_file)]),
        "tar_roundtrip_ok",
    )
    assert_ok(
        run_bb(busybox, ["tar", "-xvvOf", str(tar_file)]),
        "tar_roundtrip_ok",
    )
    tar_dst = work / "tar_dst"
    tar_dst.mkdir()
    assert_ok(run_bb(busybox, ["tar", "-xf", str(tar_file), "-C", str(tar_dst)]))
    assert (tar_dst / "tar_item.txt").read_text(encoding="utf-8") == f"tar_roundtrip_ok{NL}"
    assert_ok(
        run_bb(busybox, ["tar", "-xf", str(tar_file), "--to-command=cat"]),
        "tar_roundtrip_ok",
    )
    subprocess.run(
        _bb_cmd(busybox, ["tar", "-xf", str(tar_file), "--to-command=true"]),
        env={"PATH": os.environ.get("PATH", ""), "SHELL": ""},
        capture_output=True,
        check=True,
    )
    for bad_to_cmd in ("exit 2", "kill -9 $$"):
        assert (
            run_bb(busybox, ["tar", "-xf", str(tar_file), f"--to-command={bad_to_cmd}"]).returncode
            != 0
        )
    assert run_bb(busybox, ["tar", "-tf", str(work / "missing_archive.tar")]).returncode != 0

    excl_file = work / "excl.txt"
    excl_file.write_text(f"excluded.txt{NL}skip_dir/sub{NL}", encoding="utf-8")
    filter_tar = work / "filter.tar"
    with tarfile.open(filter_tar, "w", format=tarfile.GNU_FORMAT) as tf:
        for member_name in (
            "excluded.txt",
            "skip_dir/sub/item.txt",
            "keep_dir/sub/item.txt",
            "ctrl\x01\x7f.txt",
            "a" * 1100 + "/leaf.txt",
        ):
            tinfo = tarfile.TarInfo(member_name)
            tinfo.size = 0
            tf.addfile(tinfo, io.BytesIO(b""))
    assert_ok(run_bb(busybox, ["tar", "-tvf", str(filter_tar), "-X", str(excl_file)]))
    assert (
        run_bb(
            busybox,
            ["tar", "-tf", str(filter_tar), "-X", str(excl_file), "keep_dir/sub/", ""],
        ).returncode
        != 0
    )
    assert_ok(run_bb(busybox, ["tar", "-t"], stdin_data=filter_tar.read_bytes()))

    bad_hdr = bytearray(512)
    bad_hdr[0:7] = b"bad.txt"
    bad_hdr[100:107] = b"0000644"
    bad_hdr[108:115] = b"0000000"
    bad_hdr[116:123] = b"0000000"
    bad_hdr[124:135] = b"-10000\x00    "
    bad_hdr[136:147] = b"00000000000"
    bad_hdr[148:156] = b"        "
    bad_hdr[156] = ord("0")
    bad_hdr[257:263] = b"ustar\x00"
    bad_hdr[263:265] = b"00"
    chk = sum(bad_hdr)
    bad_hdr[148:155] = f"{chk:06o}\x00".encode("ascii")
    bad_seek_tar = work / "bad_seek.tar"
    bad_seek_tar.write_bytes(bytes(bad_hdr) + b"\x00" * 1024)
    assert run_bb(busybox, ["tar", "-tf", str(bad_seek_tar)]).returncode != 0

    unsafe_tar = work / "unsafe.tar"
    with tarfile.open(unsafe_tar, "w") as tf:
        for uname in ("/a", "./b", "..c", "../d", "e/..", "f/..name/g", "h/../i"):
            tinfo = tarfile.TarInfo(uname)
            tinfo.size = 0
            tf.addfile(tinfo, io.BytesIO(b""))
    assert_ok(run_bb(busybox, ["tar", "-tf", str(unsafe_tar)]))

    links_tar = work / "links.tar"
    with tarfile.open(links_tar, "w") as tf:
        reg_info = tarfile.TarInfo("real.txt")
        reg_info.size = 3
        tf.addfile(reg_info, io.BytesIO(b"abc"))
        for sname, starget, stype in (
            ("rel_sym", "real.txt", tarfile.SYMTYPE),
            ("abs_sym", str(work), tarfile.SYMTYPE),
            ("dot_sym", "../real.txt", tarfile.SYMTYPE),
            ("hard_link", "real.txt", tarfile.LNKTYPE),
        ):
            linfo = tarfile.TarInfo(sname)
            linfo.type = stype
            linfo.linkname = starget
            tf.addfile(linfo)
    assert_ok(run_bb(busybox, ["tar", "-tvf", str(links_tar)]), "->")
    links_dst = work / "links_dst"
    links_dst.mkdir()
    run_bb(busybox, ["tar", "-xf", str(links_tar), "-C", str(links_dst)])

    for err_idx, (ename, etarget, etype) in enumerate(
        (
            ("rel_sym", "real.txt", tarfile.SYMTYPE),
            ("dot_sym", "../real.txt", tarfile.SYMTYPE),
            ("bad_hard", "nonexistent_target.txt", tarfile.LNKTYPE),
        )
    ):
        err_tar = work / f"err_link_{err_idx}.tar"
        with tarfile.open(err_tar, "w") as tf:
            einfo = tarfile.TarInfo(ename)
            einfo.type = etype
            einfo.linkname = etarget
            tf.addfile(einfo)
        err_dst = work / f"err_dst_{err_idx}"
        err_dst.mkdir()
        (err_dst / ename).write_text("occupied", encoding="utf-8")
        assert (
            run_bb(busybox, ["tar", "-k", "-xf", str(err_tar), "-C", str(err_dst)]).returncode != 0
        )

    ar_member = work / "member.txt"
    ar_member.write_bytes(b"abc")
    os.utime(ar_member, (100000000000, 100000000000))
    ar_member2 = work / "member2.txt"
    ar_member2.write_text(f"ar_member2_payload{NL}", encoding="utf-8")
    ar_file = work / "libtest.a"
    assert_ok(run_bb(busybox, ["ar", "cr", str(ar_file), "member.txt", "member2.txt"], cwd=work))
    assert_ok(run_bb(busybox, ["ar", "r", str(ar_file), "member2.txt"], cwd=work))
    assert_ok(
        run_bb(busybox, ["ar", "t", str(ar_file), "member2.txt", "no_member.txt"], cwd=work),
        "member2.txt",
    )
    assert_ok(run_bb(busybox, ["ar", "-tv", str(ar_file)], cwd=work), "member.txt")
    assert_ok(
        run_bb(busybox, ["ar", "p", str(ar_file), "member.txt"], cwd=work),
        "abc",
    )
    ar_dst = work / "ar_dst"
    ar_dst.mkdir()
    assert_ok(run_bb(busybox, ["ar", "-xo", str(ar_file)], cwd=ar_dst))
    assert (ar_dst / "member.txt").read_bytes() == b"abc"
    for bad_ar_args in (["ar"], ["ar", ""], ["ar", "-pt", str(ar_file)]):
        assert run_bb(busybox, bad_ar_args, cwd=work).returncode != 0
    sparse_big = work / "sparse_big.bin"
    sparse_big.write_bytes(b"")
    os.truncate(sparse_big, 10000000000)
    try:
        assert (
            run_bb(
                busybox, ["ar", "cr", str(work / "big.a"), "sparse_big.bin"], cwd=work
            ).returncode
            != 0
        )
    finally:
        sparse_big.unlink()

    def make_ar_hdr(
        name: bytes,
        mtime: bytes = b"0           ",
        uid: bytes = b"0     ",
        gid: bytes = b"0     ",
        mode: bytes = b"100644  ",
        size: bytes = b"4         ",
        magic: bytes = b"`\n",
    ) -> bytes:
        """Construct a 60-byte UNIX ar member header.

        Args:
            name: Member name field (16 bytes).
            mtime: Modification timestamp field (12 bytes).
            uid: Owner user ID field (6 bytes).
            gid: Owner group ID field (6 bytes).
            mode: Octal file mode field (8 bytes).
            size: Decimal byte size field (10 bytes).
            magic: Header trailer magic bytes (2 bytes).

        Returns:
            Encoded 60-byte ``ar`` member header.
        """
        return name.ljust(16, b" ") + mtime + uid + gid + mode + size + magic

    gnu_ar = work / "gnu_ext.a"
    long_tbl = b"very_long_filename_item.txt/\n\n"
    gnu_ar.write_bytes(
        b"!<arch>\n"
        + make_ar_hdr(b"/               ", size=b"4         ")
        + b"\x00\x00\x00\x00"
        + make_ar_hdr(b"//              ", size=f"{len(long_tbl):<10d}".encode("ascii"))
        + long_tbl
        + make_ar_hdr(b"/0              ", uid=b"100000", gid=b"100000", size=b"4         ")
        + b"data"
    )
    assert_ok(run_bb(busybox, ["ar", "r", str(gnu_ar), "member.txt"], cwd=work))
    for bad_ar_payload in (
        b"not_an_ar_archive",
        b"!<arch>\n" + make_ar_hdr(b"a.txt", magic=b"xx") + b"data",
        b"!<arch>\n" + make_ar_hdr(b"a.txt", magic=b"`x") + b"data",
        b"!<arch>\n" + make_ar_hdr(b"a.txt", size=b"bad_size  ") + b"data",
        b"!<arch>\n" + make_ar_hdr(b"/99             ", size=b"4         ") + b"data",
    ):
        bad_ar = work / "bad.a"
        bad_ar.write_bytes(bad_ar_payload)
        assert run_bb(busybox, ["ar", "t", str(bad_ar)], cwd=work).returncode != 0

    # --- Extended bzip2 / bunzip2 coverage ---
    bz_rle0 = run_bb(
        busybox,
        ["bzip2", "-1", "-c"],
        stdin_data=b"A" + b"B" * 300 + b"CC" + b"DDD" + b"EEEE",
    ).stdout
    assert_ok(run_bb(busybox, ["bzcat"], stdin_data=bz_rle0))
    bz_single = run_bb(busybox, ["bzip2", "-1", "-c"], stdin_data=payload).stdout
    bz_concat = bz_single + bz_single
    assert_ok(run_bb(busybox, ["bzcat"], stdin_data=bz_concat))
    assert_ok(run_bb(busybox, ["bzcat"], stdin_data=bz_single + b"X"))
    assert_ok(run_bb(busybox, ["bzcat"], stdin_data=bz_single + b"BY"))
    bz_large = (bytes(range(256)) * 20) + (b"R" * 600)
    assert_ok(
        run_bb(
            busybox,
            ["bzcat"],
            stdin_data=run_bb(busybox, ["bzip2", "-1", "-c"], stdin_data=bz_large).stdout,
        )
    )
    for bad_bz in (
        b"BZh01234",
        b"BZh100000000",
        b"BZh10000000000",
        bz_single[:10],
        bz_single[:10] + bytes([bz_single[10] ^ 0xFF]) + bz_single[11:],
        bz_single[:-2] + bytes([bz_single[-2] ^ 0xFF, bz_single[-1]]),
    ):
        assert run_bb(busybox, ["bzcat"], stdin_data=bad_bz).returncode != 0

    # --- Extended lzop / lzo1x_c / lzo1x_d coverage ---
    lzo_lit_long = (
        b"____"
        + b"".join(struct.pack("<I", i) for i in range(1, 100))
        + struct.pack("<I", 1)
        + (b"Z" * 20)
    )
    lzo_m4_streams = (
        b"____"
        + b"KLMNO"
        + (b"\x00" * 20000)
        + b"KLMNO"
        + b"____"
        + (b"PQ" * 150)
        + (b"\x00" * 20000)
        + (b"PQ" * 150)
        + b"____"
        + b"WXYZ"
        + (b"\x00" * 50000)
        + b"WXYZ"
        + (b"Z" * 20)
    )
    lzo_rich = (
        b"ABCD"
        + b"x" * 2
        + b"ABCD"
        + b"y" * 10
        + b"ABCD"
        + b"z" * 600
        + b"ABCD"
        + (b"M" * 400)
        + b"123"
        + (b"N" * 400)
        + bytes(i % 251 for i in range(3000))
        + (b"M" * 400)
        + b"4567890123"
        + (b"P" * 20000)
        + bytes((i * 7) % 253 for i in range(18000))
        + (b"Q" * 400)
        + bytes((i * 13) % 251 for i in range(20000))
        + (b"Q" * 400)
    )
    for lzo_in in (
        b"",
        b"short",
        lzo_lit_long,
        lzo_m4_streams,
        lzo_rich,
        lzo_rich + b"T",
        lzo_rich + b"T" * 10,
        lzo_rich + b"T" * 600,
        os.urandom(4096),
    ):
        lzo_packed = run_bb(busybox, ["lzop", "-c"], stdin_data=lzo_in).stdout
        assert run_bb(busybox, ["lzop", "-dc"], stdin_data=lzo_packed).stdout == lzo_in
    lzo_c_packed = run_bb(busybox, ["lzop", "-C", "-c"], stdin_data=payload).stdout
    assert_ok(run_bb(busybox, ["lzop", "-dc"], stdin_data=lzo_c_packed))
    assert_ok(run_bb(busybox, ["lzop", "-F", "-dc"], stdin_data=lzo_c_packed))
    lzo_f_packed = run_bb(busybox, ["lzop", "-F", "-c"], stdin_data=payload).stdout
    assert_ok(run_bb(busybox, ["lzop", "-dc"], stdin_data=lzo_f_packed))
    lzo_u_file = work / "lzo_u.txt"
    lzo_u_file.write_bytes(payload)
    assert_ok(run_bb(busybox, ["lzop", "-U", "-k", str(lzo_u_file)], cwd=work))
    for alt_lzo_name in ("lzo_noext", "lzo_bad.ext"):
        alt_lzo_path = work / alt_lzo_name
        alt_lzo_path.write_bytes(lzo_f_packed)
        assert_ok(run_bb(busybox, ["unlzop", str(alt_lzo_path)], cwd=work))
        assert (work / f"{alt_lzo_name}.out").read_bytes() == payload
    assert run_bb(busybox, ["lzop", "-7", "-c"], stdin_data=payload).returncode != 0

    LZO_MAGIC = b"\x89LZO\x00\r\n\x1a\n"
    valid_lzo = run_bb(busybox, ["lzop", "-c"], stdin_data=b"hello_lzo").stdout
    # Header with filename length > 0
    hdr_body = bytearray(valid_lzo[9:33])
    hdr_body.append(4)
    hdr_body.extend(b"test")
    adler_val = zlib.adler32(bytes(hdr_body), 1) & 0xFFFFFFFF
    lzo_with_name = LZO_MAGIC + bytes(hdr_body) + struct.pack(">I", adler_val) + valid_lzo[38:]
    assert_ok(run_bb(busybox, ["lzop", "-dc"], stdin_data=lzo_with_name))

    for bad_lzo in (
        b"bad_magic",
        LZO_MAGIC + b"\x09\x40\x20\x30\x09\x40\x99" + b"\x00" * 30,
        valid_lzo[:34] + b"\xde\xad\xbe\xef",
        valid_lzo[:38] + struct.pack(">I", 0xFFFFFFFF),
        valid_lzo[:38] + struct.pack(">I", 70 * 1024 * 1024),
        valid_lzo[:38] + struct.pack(">II", 10, 0),
        valid_lzo[:38] + struct.pack(">II", 10, 20),
        valid_lzo[:-5] + bytes([valid_lzo[-5] ^ 0xFF]) + valid_lzo[-4:],
    ):
        assert run_bb(busybox, ["lzop", "-dc"], stdin_data=bad_lzo).returncode != 0

    def make_custom_lzo(comp_block: bytes, dst_len: int) -> bytes:
        """Build a single-block lzop stream with a custom LZO1X payload block.

        Args:
            comp_block: Raw LZO1X-compressed block bytes.
            dst_len: Declared uncompressed block size in bytes.

        Returns:
            Complete lzop archive byte stream.
        """
        return (
            lzo_f_packed[:38]
            + struct.pack(">II", dst_len, len(comp_block))
            + comp_block
            + struct.pack(">I", 0)
        )

    # Custom LZO blocks for lzo1x_d edge cases and error labels
    # 1. Trailing bytes after EOF -> LZO_E_INPUT_NOT_CONSUMED
    eof_trailing = bytes([18, ord("A"), 0x11, 0x00, 0x00, 0x99])
    assert (
        run_bb(
            busybox, ["lzop", "-F", "-dc"], stdin_data=make_custom_lzo(eof_trailing, 10)
        ).returncode
        != 0
    )
    # 2. Input overrun (copy_next requests more bytes than in block)
    in_overrun = bytes([25, ord("A")])
    assert (
        run_bb(
            busybox, ["lzop", "-F", "-dc"], stdin_data=make_custom_lzo(in_overrun, 20)
        ).returncode
        != 0
    )
    # 3. Output overrun (match copies more bytes than dst_len while src_len < dst_len)
    out_overrun = bytes([18, ord("A"), 0x20, 0x05, 0x00, 0x00])
    assert (
        run_bb(
            busybox, ["lzop", "-F", "-dc"], stdin_data=make_custom_lzo(out_overrun, 10)
        ).returncode
        != 0
    )
    # 4. Lookbehind overrun (match points before start of output)
    lb_overrun = bytes([18, ord("A"), 0x44, 0x00, 0x11, 0x00, 0x00])
    assert (
        run_bb(
            busybox, ["lzop", "-F", "-dc"], stdin_data=make_custom_lzo(lb_overrun, 20)
        ).returncode
        != 0
    )
    # 5. Block size > 256KB followed by normal block (realloc and reuse)
    big_two_blocks = (
        lzo_f_packed[:38]
        + struct.pack(">II", 300000, 300000)
        + (b"A" * 300000)
        + struct.pack(">II", 10, 10)
        + (b"B" * 10)
        + struct.pack(">I", 0)
    )
    assert_ok(run_bb(busybox, ["lzop", "-F", "-dc"], stdin_data=big_two_blocks))

    # --- Extended xz coverage ---
    xz_crc64 = lzma.compress(payload, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64)
    assert_ok(run_bb(busybox, ["xz", "-dc"], stdin_data=xz_crc64))
    xz_none = lzma.compress(payload, format=lzma.FORMAT_XZ, check=lzma.CHECK_NONE)
    assert_ok(run_bb(busybox, ["xz", "-dc"], stdin_data=xz_none))
    # Custom XZ stream with uncompressed LZMA2 chunk (0x01), block padding, and VLI > 127
    raw_msg = b"U" * 130
    lzma2_uncomp_raw = bytes([0x01]) + struct.pack(">H", len(raw_msg) - 1) + raw_msg + b"\x00"
    lzma2_uncomp_padded = lzma2_uncomp_raw
    while len(lzma2_uncomp_padded) % 4 != 0:
        lzma2_uncomp_padded += b"\x00"
    sh_flags = b"\x00\x00"
    sh = b"\xfd7zXZ\x00" + sh_flags + struct.pack("<I", zlib.crc32(sh_flags) & 0xFFFFFFFF)
    # Block header with flags=0xC0 (compressed and uncompressed VLIs >= 128)
    bh_body = bytearray(
        [
            0x02,
            0xC0,
            (len(lzma2_uncomp_raw) & 0x7F) | 0x80,
            len(lzma2_uncomp_raw) >> 7,
            (len(raw_msg) & 0x7F) | 0x80,
            len(raw_msg) >> 7,
            0x21,
            0x00,
        ]
    )
    bh = bytes(bh_body) + struct.pack("<I", zlib.crc32(bytes(bh_body)) & 0xFFFFFFFF)
    assert_ok(run_bb(busybox, ["xz", "-dc"], stdin_data=xz_crc64 + xz_none))
    # Also corrupt block padding byte and index padding byte in xz_crc64 / custom stream
    bad_blk_pad = (
        sh
        + bh
        + lzma2_uncomp_raw
        + b"\xff" * (len(lzma2_uncomp_padded) - len(lzma2_uncomp_raw))
        + b"\x00" * 20
    )
    bad_idx_pad = (
        sh
        + bh
        + lzma2_uncomp_padded
        + bytes(
            [
                0x00,
                0x01,
                (len(bh) + len(lzma2_uncomp_raw)) & 0x7F | 0x80,
                (len(bh) + len(lzma2_uncomp_raw)) >> 7,
                (len(raw_msg) & 0x7F) | 0x80,
                len(raw_msg) >> 7,
                0xFF,
                0x00,
            ]
        )
    )
    # Also corrupt block CRC32 in xz_archive (CRC32 check stream)
    xz_crc32_bytes = lzma.compress(b"A", format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32)
    # In xz_crc32_bytes: 12B stream hdr + 12B block hdr + 8B data + 4B block CRC32
    bad_blk_crc = xz_crc32_bytes[:32] + b"\xde\xad\xbe\xef" + xz_crc32_bytes[36:]
    # Corrupt index hash by changing uncompressed size VLI in index (at offset 39)
    bad_idx_hash = xz_crc32_bytes[:39] + bytes([xz_crc32_bytes[39] ^ 0x02]) + xz_crc32_bytes[40:]
    # Corrupt index CRC32 (at offset 44..48, right before 12B footer)
    bad_idx_crc = xz_crc32_bytes[:-16] + b"\xde\xad\xbe\xef" + xz_crc32_bytes[-12:]
    for bad_xz in (
        xz_crc64[:6],
        sh,
        b"\xfd7zXZ\x00\x00\x00\xde\xad\xbe\xef",
        sh + b"\x02\x00\x21\x00\x00\x00\x00\x00\xde\xad\xbe\xef",
        sh + bh + lzma2_uncomp_padded,
        bad_blk_pad,
        bad_idx_pad,
        bad_blk_crc,
        bad_idx_hash,
        bad_idx_crc,
        xz_crc64[:-6] + b"\xff\xff\xff\xff" + xz_crc64[-2:],
        xz_crc64[:-2] + b"XX",
    ):
        assert run_bb(busybox, ["xz", "-dc"], stdin_data=bad_xz).returncode != 0

    # --- Extended tar & get_header_tar coverage ---
    tar_ext_src = work / "tar_ext_src"
    tar_ext_src.mkdir()
    f_hard1 = tar_ext_src / "hard1.txt"
    f_hard1.write_text("hardlink_data\n", encoding="utf-8")
    f_hard2 = tar_ext_src / "hard2.txt"
    os.link(f_hard1, f_hard2)
    f_hard3 = tar_ext_src / "hard3.txt"
    f_hard3.write_text("hardlink_data_2\n", encoding="utf-8")
    f_hard4 = tar_ext_src / "hard4.txt"
    os.link(f_hard3, f_hard4)
    long_dir = tar_ext_src / ("d" * 105)
    long_dir.mkdir()
    (long_dir / "file_in_long_dir.txt").write_text("long_dir_data\n", encoding="utf-8")
    long_sym = tar_ext_src / "long_sym"
    os.symlink("t" * 105, long_sym)
    short_sym = tar_ext_src / "short_sym"
    os.symlink("hard1.txt", short_sym)
    incl_list = work / "tar_incl.txt"
    incl_list.write_text(f"{tar_ext_src}/hard1.txt/\n{tar_ext_src}\n", encoding="utf-8")
    excl_list2 = work / "tar_excl2.txt"
    excl_list2.write_text(f"{str(tar_ext_src).lstrip('/')}/excluded_item\n", encoding="utf-8")
    (tar_ext_src / "excluded_item").write_text("skip\n", encoding="utf-8")

    ext_tar = work / "ext.tar"
    assert_ok(
        run_bb(
            busybox,
            [
                "tar",
                "cfv",
                str(ext_tar),
                "-T",
                str(incl_list),
                "-X",
                str(excl_list2),
                str(ext_tar),
            ],
        )
    )
    assert_ok(run_bb(busybox, ["tar", "cv", "-T", str(incl_list)]))
    dash_tar = work / "-dash.tar"
    dash_tar.write_bytes(ext_tar.read_bytes())
    assert_ok(run_bb(busybox, ["tar", "-tf", "-dash.tar"], cwd=work))
    assert (
        run_bb(
            busybox,
            [
                "tar",
                "-tf",
                str(filter_tar),
                "-X",
                str(excl_file),
                "excluded.txt",
                "keep_dir/sub/item.txt",
                "",
            ],
        ).returncode
        != 0
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "tar",
                "-cf",
                str(work / "deref.tar"),
                "-h",
                "--no-recursion",
                "//" + str(f_hard1).lstrip("/"),
            ],
        )
    )
    assert run_bb(busybox, ["tar", "-cf", str(work / "empty.tar")]).returncode != 0
    assert (
        run_bb(
            busybox, ["tar", "-cf", str(work / "missing_in.tar"), str(work / "no_such_input")]
        ).returncode
        != 0
    )

    # Test tar -z, -j, -J decompression
    tar_bytes = ext_tar.read_bytes()
    gz_tar = work / "ext.tar.gz"
    gz_tar.write_bytes(run_bb(busybox, ["gzip", "-c"], stdin_data=tar_bytes).stdout)
    assert_ok(run_bb(busybox, ["tar", "-ztf", str(gz_tar)]))
    bz_tar = work / "ext.tar.bz2"
    bz_tar.write_bytes(run_bb(busybox, ["bzip2", "-c"], stdin_data=tar_bytes).stdout)
    assert_ok(run_bb(busybox, ["tar", "-jtf", str(bz_tar)]))
    xz_tar = work / "ext.tar.xz"
    xz_tar.write_bytes(lzma.compress(tar_bytes, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32))
    assert_ok(run_bb(busybox, ["tar", "-Jtf", str(xz_tar)]))

    # Custom tar headers for typeflags 3, 4, 6, 7, g, x, K, D, prefix, and single zero block
    def make_tar_block(
        name: bytes,
        typeflag: bytes,
        size: int = 0,
        linkname: bytes = b"",
        prefix: bytes = b"",
        mode: bytes = b"0000644",
    ) -> bytes:
        """Build a 512-byte POSIX ustar header block with a computed octal checksum.

        Args:
            name: Entry file name bytes.
            typeflag: Single-byte tar typeflag indicator.
            size: Entry payload size in bytes.
            linkname: Target path bytes for hard/symbolic links.
            prefix: Ustar path prefix bytes.
            mode: Octal file mode field bytes.

        Returns:
            512-byte tar header block.
        """
        blk = bytearray(512)
        blk[0 : len(name)] = name
        blk[100 : 100 + len(mode)] = mode
        blk[108:115] = b"0000000"
        blk[116:123] = b"0000000"
        blk[124:135] = f"{size:011o}".encode("ascii")
        blk[136:147] = b"00000000000"
        blk[148:156] = b"        "
        blk[156:157] = typeflag
        blk[157 : 157 + len(linkname)] = linkname
        blk[257:263] = b"ustar\x00"
        blk[263:265] = b"00"
        blk[329:336] = b"0000001"
        blk[337:344] = b"0000002"
        blk[345 : 345 + len(prefix)] = prefix
        chk_val = sum(blk)
        blk[148:155] = f"{chk_val:06o}\x00".encode("ascii")
        return bytes(blk)

    long_target_bytes = b"long_link_target_" + b"x" * 100 + b"\x00"
    long_target_padded = long_target_bytes.ljust(512, b"\x00")
    custom_tar_stream = (
        b"\x00" * 512
        + make_tar_block(b"chr_dev", b"3", mode=b"000644 ")
        + make_tar_block(b"blk_dev", b"4")
        + make_tar_block(b"fifo_node", b"6")
        + make_tar_block(b"contig_dir/", b"7")
        + make_tar_block(b"pax_g", b"g", size=5)
        + b"12345".ljust(512, b"\x00")
        + make_tar_block(b"gnu_d", b"D", size=0)
        + make_tar_block(b"././@LongLink", b"K", size=len(long_target_bytes))
        + long_target_padded
        + make_tar_block(b"prefixed_sym", b"2", prefix=b"my_prefix")
        + b"\x00" * 1024
    )
    assert_ok(run_bb(busybox, ["tar", "-tv"], stdin_data=custom_tar_stream))
    bad_type_tar = make_tar_block(b"bad_type", b"Z") + b"\x00" * 1024
    assert run_bb(busybox, ["tar", "-t"], stdin_data=bad_type_tar).returncode != 0
    bad_octal_tar = make_tar_block(b"bad_octal", b"0", mode=b"0000999") + b"\x00" * 1024
    assert run_bb(busybox, ["tar", "-t"], stdin_data=bad_octal_tar).returncode != 0
    assert (
        run_bb(busybox, ["tar", "-t"], stdin_data=b"bad_tar_block".ljust(512, b"X")).returncode != 0
    )

    # --- Extended gzip & decompress_gunzip coverage ---
    for small_gz_in in (
        b"a",
        (b"ABCD_123456789_" + bytes(range(1, 100))) * 10,
        b"".join(b"abc" + bytes([1 + (i & 0xFE), 1 + ((i >> 8) & 0xFE)]) for i in range(9000)),
    ):
        small_gz = run_bb(busybox, ["gzip", "-c"], stdin_data=small_gz_in).stdout
        assert run_bb(busybox, ["gunzip", "-c"], stdin_data=small_gz).stdout == small_gz_in
    # 1. Stored block > 32KB & Huffman-only > 32KB (triggers GUNZIP_WSIZE window flushes)
    rand_block = b"".join(
        zlib.crc32(f"blk_{i}".encode("ascii")).to_bytes(4, "little")
        + struct.pack("<I", (i * 2654435761) & 0xFFFFFFFF)
        for i in range(4500)
    )[:35000]
    raw_stored_comp = zlib.compressobj(level=0, wbits=-15)
    raw_stored_deflate = raw_stored_comp.compress(rand_block) + raw_stored_comp.flush()
    stored_gz = (
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"
        + raw_stored_deflate
        + struct.pack("<II", zlib.crc32(rand_block) & 0xFFFFFFFF, len(rand_block))
    )
    assert run_bb(busybox, ["gunzip", "-c"], stdin_data=stored_gz).stdout == rand_block
    huff_lit_data = b"L" * 33000
    raw_huff_comp = zlib.compressobj(level=1, wbits=-15, strategy=zlib.Z_HUFFMAN_ONLY)
    raw_huff_deflate = raw_huff_comp.compress(huff_lit_data) + raw_huff_comp.flush()
    huff_lit_gz = (
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"
        + raw_huff_deflate
        + struct.pack("<II", zlib.crc32(huff_lit_data) & 0xFFFFFFFF, len(huff_lit_data))
    )
    assert run_bb(busybox, ["gunzip", "-c"], stdin_data=huff_lit_gz).stdout == huff_lit_data
    big_gz_in = (
        rand_block[:20000]
        + (b"ABCDEFGHIJ" * 4000)
        + bytes(i % 256 for i in range(40000))
        + (b"Z" * 15000)
    )
    big_gz = run_bb(busybox, ["gzip", "-c"], stdin_data=big_gz_in).stdout
    assert run_bb(busybox, ["gunzip", "-c"], stdin_data=big_gz).stdout == big_gz_in
    # 2. Fixed Huffman block (small zlib raw stream wrapped in gzip)
    fixed_raw = zlib.compressobj(level=1, wbits=-15)
    fixed_deflate = fixed_raw.compress(b"fixed_huffman_short") + fixed_raw.flush()
    crc_fixed = zlib.crc32(b"fixed_huffman_short") & 0xFFFFFFFF
    # Also include FEXTRA (0x04), FNAME (0x08), FCOMMENT (0x10), FHCRC (0x02) -> flags = 0x1E
    gz_all_flags = (
        b"\x1f\x8b\x08\x1e\x00\x00\x00\x00\x00\x03"
        + struct.pack("<H", 4)
        + b"XTRA"
        + b"orig_name.txt\x00"
        + b"my_comment\x00"
        + b"\x12\x34"
        + fixed_deflate
        + struct.pack("<II", crc_fixed, len(b"fixed_huffman_short"))
    )
    # Concatenated gzip streams & trailing non-gzip bytes
    assert_ok(run_bb(busybox, ["gunzip", "-c"], stdin_data=gz_all_flags + gz_all_flags))
    assert_ok(run_bb(busybox, ["gunzip", "-c"], stdin_data=gz_all_flags + b"\x1f\x00"))
    assert_ok(run_bb(busybox, ["gunzip", "-c"], stdin_data=gz_all_flags + b"\x00\x00"))
    assert_ok(run_bb(busybox, ["gzip", "-dc"], stdin_data=gz_all_flags))
    for bad_gz in (
        b"\x1f\x8b\x08",
        b"\x1f\x8b\x99\x00\x00\x00\x00\x00\x00\x03",
        gz_all_flags[:45],
        gz_all_flags[:-8] + b"\x00\x00",
        gz_all_flags[:-8] + struct.pack("<II", crc_fixed ^ 1, len(b"fixed_huffman_short")),
        gz_all_flags[:-4] + struct.pack("<I", 9999),
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x07\x00\x00\x00",
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x01\x01\x00\x00\x00",
    ):
        assert run_bb(busybox, ["gunzip", "-c"], stdin_data=bad_gz).returncode != 0

    # --- Extended unzip coverage ---
    zip_ext = work / "ext_archive.zip"
    with zipfile.ZipFile(zip_ext, "w") as zf:
        z_dir = zipfile.ZipInfo("sub_dir/")
        z_dir.external_attr = (0o040755 << 16) | 0x10
        z_dir.create_system = 3
        zf.writestr(z_dir, b"")
        z_stored = zipfile.ZipInfo("sub_dir/stored.txt")
        z_stored.compress_type = zipfile.ZIP_STORED
        z_stored.extra = b"extra_bytes"
        zf.writestr(z_stored, b"stored_payload\n")
        z_empty = zipfile.ZipInfo("sub_dir/empty.txt")
        z_empty.create_system = 0
        z_empty.compress_type = zipfile.ZIP_STORED
        zf.writestr(z_empty, b"")
        z_sym = zipfile.ZipInfo("sub_dir/sym_link")
        z_sym.create_system = 3
        z_sym.external_attr = 0o120777 << 16
        z_sym.compress_type = zipfile.ZIP_STORED
        zf.writestr(z_sym, b"stored.txt")

    unzip_ext_dst = work / "unzip_ext_dst"
    assert_ok(run_bb(busybox, ["unzip", str(zip_ext), "-d", str(unzip_ext_dst)]))
    assert_ok(run_bb(busybox, ["unzip", "-q", str(zip_ext), "-d", str(work / "unzip_quiet_dst")]))
    assert_ok(run_bb(busybox, ["unzip", "-q", "-o", str(zip_ext), "-d", str(unzip_ext_dst)]))
    # Re-extract with prompt: invalid response 'z', then 'n', then 'N', then 'A'
    assert_ok(
        run_bb(
            busybox,
            ["unzip", str(zip_ext), "-d", str(unzip_ext_dst)],
            stdin_data=b"z\nn\nN\n",
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["unzip", str(zip_ext), "-d", str(unzip_ext_dst)],
            stdin_data=b"A\n",
        )
    )
    assert_ok(run_bb(busybox, ["unzip", "-n", str(zip_ext), "-d", str(unzip_ext_dst)]))
    assert_ok(
        run_bb(
            busybox, ["unzip", "-p", str(zip_ext), "sub_dir/stored.txt", "-x", "sub_dir/empty.txt"]
        )
    )
    assert_ok(run_bb(busybox, ["unzip", "-l", str(work / "ext_archive")]))
    assert_ok(run_bb(busybox, ["unzip", "-lq", str(zip_ext)]))
    dash_zip = work / "-dash.zip"
    dash_zip.write_bytes(zip_path.read_bytes())
    assert_ok(run_bb(busybox, ["unzip", "-l", "--", "-dash.zip"], cwd=work))
    assert_ok(run_bb(busybox, ["unzip", "-"], stdin_data=zip_path.read_bytes()))
    # Corrupt CRC in zip_path (CRC is at offset 14..18 of local file header)
    raw_zip_bytes = bytearray(zip_path.read_bytes())
    raw_zip_bytes[14] ^= 0xFF
    assert run_bb(busybox, ["unzip", "-"], stdin_data=bytes(raw_zip_bytes)).returncode != 0
    for bad_unzip_args in (
        ["unzip"],
        ["unzip", "-z"],
        ["unzip", str(zip_ext), "-d"],
        ["unzip", str(work / "nonexistent_zip_xyz")],
    ):
        assert run_bb(busybox, bad_unzip_args).returncode != 0


def run_procps_and_shell_suite(busybox: Path, work: Path) -> None:
    """Verify procps, miscutils, and shell applets (`ash`, `hush`, `sh`).

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        work: Isolated temporary directory for test fixtures.
    """
    _ = work
    assert_ok(run_bb(busybox, ["free"]))
    for free_flag in ("-b", "-k", "-m", "-g", "-h"):
        assert_ok(run_bb(busybox, ["free", free_flag]))
    assert run_bb(busybox, ["free", "-x"]).returncode != 0
    assert_ok(run_bb(busybox, ["uptime"]), "up")
    assert_ok(run_bb(busybox, ["uptime", "-s"]))
    assert_ok(run_bb(busybox, ["ps"]), "PID")
    assert_ok(run_bb(busybox, ["ps", "-l"]), "PID")
    assert_ok(
        run_bb(
            busybox,
            [
                "ps",
                "-o",
                "user,group,comm,args,pid,ppid,pgid,etime,nice,rgroup,ruser,time,tty,vsz,sid,stat,rss",
            ],
        )
    )
    assert_ok(run_bb(busybox, ["ps", "-o", "pid=MYPID,user=,comm=CMD"]))
    assert_ok(run_bb(busybox, ["ps", "-o", "pid=,user="]))
    assert run_bb(busybox, ["ps", "-o", "invalid_col_xyz"]).returncode != 0
    assert run_bb(busybox, ["kill"]).returncode != 0
    assert run_bb(busybox, ["kill", "-BADSIG", "1"]).returncode != 0
    assert run_bb(busybox, ["kill", "-HUP", "not_a_pid"]).returncode != 0
    assert run_bb(busybox, ["kill", "not_a_pid"]).returncode != 0
    assert_ok(run_bb(busybox, ["kill", "-l"]), "HUP")
    for sig_arg in (
        "1",
        "0",
        "HUP",
        "SIGHUP",
        "IO",
        "IOT",
        "SIGIO",
        "SIGIOT",
        "99999",
        "BADSIG",
        "IOX",
        "IOTX",
    ):
        run_bb(busybox, ["kill", "-l", sig_arg])
    run_bb(busybox, ["kill", "-HUP", "9999999"])
    run_bb(busybox, ["kill", "-HUP", "1"])
    run_bb(busybox, ["killall", "-0", "busybox", "nonexistent_proc_xyz_987"])
    run_bb(busybox, ["killall", "nonexistent_proc_xyz_987"])
    assert_ok(run_bb(busybox, ["killall", "-l"]), "HUP")
    assert_ok(run_bb(busybox, ["time", "echo", "timed_ok", "arg2"]), "timed_ok")
    assert_ok(run_bb(busybox, ["time", "-v", "echo", "timed_v", "arg2"]), "timed_v")
    assert_ok(run_bb(busybox, ["time", "-p", "echo", "timed_p"]), "timed_p")
    time_out_file = work / "time_out.txt"
    assert_ok(run_bb(busybox, ["time", "-o", str(time_out_file), "echo", "t1"]))
    assert_ok(run_bb(busybox, ["time", "-a", "-o", str(time_out_file), "echo", "t2"]))
    for time_fmt in (
        "lit %% %C %D %E %F %I %K %M %O %P %R %S %T %U %u %W %X %Z "
        "%c %e %k %p %r %s %t %w %x %z \\t \\n \\\\ \\z",
        "trail_pct%",
        "trail_bs\\",
    ):
        run_bb(busybox, ["time", "-f", time_fmt, "sh", "-c", "exit 2"])
    run_bb(busybox, ["time", "nonexistent_cmd_xyz_987"])

    sleeper = subprocess.Popen(
        _bb_cmd(busybox, ["sleep", "30"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    sleeper2 = subprocess.Popen(
        _bb_cmd(busybox, ["sleep", "30"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.15)
        pid_out = assert_ok(run_bb(busybox, ["pidof", "busybox"])).strip()
        assert any(tok.isdigit() for tok in pid_out.split())
        omit_args = [arg for tok in pid_out.split() if tok.isdigit() for arg in ("-o", tok)]
        run_bb(
            busybox,
            [
                "pidof",
                "-o",
                "%PPID",
                "-o",
                "999999",
                *omit_args,
                "busybox",
            ],
        )
        assert_ok(run_bb(busybox, ["pidof", "-o", "999999", "busybox"]))
        assert_ok(run_bb(busybox, ["pidof", "-s", "busybox"]))
        assert run_bb(busybox, ["pidof", "nonexistent_proc_xyz_987"]).returncode != 0
        pgrep_out = assert_ok(run_bb(busybox, ["pgrep", "busybox"])).strip()
        assert any(tok.isdigit() for tok in pgrep_out.splitlines())
        for pg_args in (
            ["pgrep", "-l", "busybox"],
            ["pgrep", "-a", "busybox"],
            ["pgrep", "-f", "sleep"],
            ["pgrep", "-x", "busybox"],
            ["pgrep", "-x", "usybox"],
            ["pgrep", "-x", "busybo"],
            ["pgrep", "-x", "busybox\n"],
            ["pgrep", "-n", "busybox"],
            ["pgrep", "-o", "busybox"],
            ["pgrep", "-s", "0"],
            ["pgrep", "-s", "1"],
            ["pgrep", "-P", "1", "-s", "999999", "busybox"],
            ["pgrep", "-P", "999999", "busybox"],
            ["pgrep", "-v", "-P", "1", "nonexistent_xyz"],
            ["pgrep", "-v", "-P", "999999", "busybox"],
            ["pgrep", "-v", "busybox"],
            ["pgrep"],
            ["pgrep", "a", "b"],
            ["pkill", "-e", "nonexistent_proc_xyz_987"],
            ["pkill", "nonexistent_proc_xyz_987"],
            ["pkill", "-e", "-P", "1", "busybox"],
            ["pkill", "-P", "1", "busybox"],
        ):
            run_bb(busybox, pg_args)
        assert_ok(run_bb(busybox, ["ps", "-o", "pid,user,group,comm,args"]))
    finally:
        sleeper.kill()
        sleeper.wait()
        sleeper2.kill()
        sleeper2.wait()

    assert_ok(run_bb(busybox, ["pkill", "-l"]), "HUP")
    assert run_bb(busybox, ["watch", "-n", "invalid_dur", "echo"]).returncode != 0
    assert_ok(run_bb(busybox, ["watch", "-n", "0", "echo", "watched_once", "arg2"]), "watched_once")
    assert_ok(run_bb(busybox, ["watch", "-n", "0", "-t", "-x", "echo", "watched_x"]), "watched_x")
    assert_ok(run_bb(busybox, ["watch", "-n", "0", "-t", "-x", "false"]))
    assert_ok(run_bb(busybox, ["watch", "-n", "0", "-t", "-x", "seq", "1"]), "1")
    assert_ok(run_bb(busybox, ["watch", "-n", "0", "-t", "-x", "/usr/bin/true"]))
    assert_ok(run_bb(busybox, ["watch", "-n", "0", "-t", "-x", "nonexistent_cmd_xyz_999"]))

    hex_empty = work / "hex_empty.bin"
    hex_empty.write_bytes(b"")
    assert_ok(run_bb(busybox, ["hexedit", str(hex_empty)], stdin_data=b"\x18"))
    hex_big = work / "hex_big.bin"
    hex_big.write_bytes(bytes(i % 256 for i in range(400000)))
    hex_keys = (
        b"\x1b[10;20R"
        b"\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b[F\x1b[5~\x1b[6~\x1b[3~"
        b"\x1bOA\x1bOB\x1bOC\x1bOD\x1bOH\x1bOF"
        b"09afAFg\x08\x7f" + (b"\x1bOB" * 4100) + (b"\x1bOA" * 20) + b"\x1b[F0\x1b[H\x0c\x18"
    )
    assert_ok(run_bb(busybox, ["hexedit", str(hex_big)], stdin_data=hex_keys))
    hex_small = work / "hex_small.bin"
    hex_small.write_bytes(b"\x00\x01\x7f\x80\xff" * 4)
    assert_ok(
        run_bb(
            busybox,
            ["hexedit", str(hex_small)],
            stdin_data=(
                (b"\x1b[D" * 5)
                + (b"\x1b[C" * 36)
                + (b"\x1b[D" * 36)
                + (b"\x1b[B" * 5)
                + (b"\x1b[C" * 30)
                + b"0123456789abcdef"
                + (b"\x1b[C" * 10)
                + b"\x1b[B\x1b[A\x18"
            ),
        )
    )
    for esc_seq in (
        b"\x1b",
        b"\x1b[",
        b"\x1b[99~",
        b"\x1b[10",
        b"\x1b[10;xR",
        b"\x1b[10;1X",
        b"\x1b[0;1R",
        b"\x1b[1;0R",
        b"\x1b[99999;1R",
    ):
        er_fd, ew_fd = os.pipe()
        e_proc = subprocess.Popen(
            _bb_cmd(busybox, ["hexedit", str(hex_small)]),
            stdin=er_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.close(er_fd)
        os.write(ew_fd, esc_seq)
        time.sleep(0.06)
        os.write(ew_fd, b"\x18")
        os.close(ew_fd)
        e_proc.communicate(timeout=3.0)
        assert e_proc.returncode == 0
    assert_ok(run_bb(busybox, ["hexedit", str(hex_small)], stdin_data=b"\n"))

    for goto_val in (
        b"16\n\x18",
        b"16384\n\x18",
        b"999999\n" + (b"\x1b[C" * 40) + b"01\x1b[B\x18",
        b"66048\n" + (b"\x1bOA" * 33) + b"\x1bOD\x1bOD\x18",
        b"33280\n" + (b"\x1bOA" * 32) + b"\x1bOD\x18",
        b"65535\n\x1bOC0\x18",
    ):
        hr_fd, hw_fd = os.pipe()
        h_proc = subprocess.Popen(
            _bb_cmd(busybox, ["hexedit", str(hex_big)]),
            stdin=hr_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.close(hr_fd)
        os.write(hw_fd, b"\n")
        time.sleep(0.05)
        nl_idx = goto_val.index(b"\n") + 1
        os.write(hw_fd, goto_val[:nl_idx])
        time.sleep(0.05)
        os.write(hw_fd, goto_val[nl_idx:])
        os.close(hw_fd)
        h_proc.communicate(timeout=3.0)
        assert h_proc.returncode == 0

    hs_r, hs_w = os.pipe()
    hex_sig_proc = subprocess.Popen(
        _bb_cmd(busybox, ["hexedit", str(hex_small)]),
        stdin=hs_r,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(hs_r)
    try:
        time.sleep(0.15)
        hex_sig_proc.terminate()
    finally:
        os.close(hs_w)
        hex_sig_proc.wait(timeout=3.0)

    grp_dir = work / "grp_test"
    grp_dir.mkdir(parents=True, exist_ok=True)
    grp_file = grp_dir / "grp_exec.sh"
    grp_file.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    grp_file.chmod(0o050)
    grp_file2 = grp_dir / "other_exec.sh"
    grp_file2.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    grp_file2.chmod(0o010)
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                f"PATH={grp_dir}:$PATH; type grp_exec.sh >/dev/null 2>&1 || true; "
                "type other_exec.sh >/dev/null 2>&1 || true; "
                f"export {'X' * 150}=longval; unset {'X' * 150}; "
                "LINES=0 COLUMNS=50000 ls >/dev/null; "
                "LINES=25 COLUMNS=90 ls >/dev/null; "
                "awk 'BEGIN { a[-5]=1; print a[-5] }' >/dev/null; "
                "echo $((20 + 22)); [ -O . ] || true; [ -G . ] || true",
            ],
        ),
        "42",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                "x=$(echo subshell_ok); cat <<EOF\nhello_heredoc_$x\nEOF\n",
            ],
        ),
        "hello_heredoc_subshell_ok",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                "for x in 1 2 3; do echo $x; done; "
                "v=ab_cd_ef; echo ${v#*_} ${v##*_} ${v%_*} ${v%%_*} ${v#zzz} ${v#} ${v#\\*} "
                "${v##z*} ${v##z\\*} ${v##z\\\\*} ${v##\\*}; "
                "echo $RANDOM $RANDOM; RANDOM=0; echo $RANDOM; RANDOM=1073741824; echo $RANDOM; "
                "read -t 0 rvar < /dev/null || true",
            ],
        ),
        f"1{NL}2{NL}3",
    )
    assert_ok(
        run_bb(
            busybox,
            ["hush", "-c", "read -t 1 rvar && read rvar2 && echo got_${rvar}_${rvar2}"],
            stdin_data=f"hello{NL}world{NL}".encode(),
        ),
        "got_hello_world",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                "export A=1 B=2; export A=3; echo $((20 + 22)); "
                "[ -O . ] || true; [ -G . ] || true; "
                "a=5; b=3; undef=''; rec1='1+rec2'; rec2='rec1'; "
                "echo $(()) $((+a)) $((-a)) $((!a)) $((!0)) $((~a)) "
                "$((++a)) $((--a)) $((a++)) $((a--)) $((--7)) $((++7)) "
                "$((a + b + 1)) $((a - b)) $((a * b)) $((a / b)) $((a % b)) $((2 ** 3)) "
                "$((a << 1)) $((a >> 1)) $((a < b)) $((a > b)) $((a <= b)) $((a >= b)) "
                "$((a == b)) $((a != b)) $((a & b)) $((a ^ b)) $((a | b)) "
                "$((1 && 2)) $((0 && 2)) $((1 && 0)) $((0 || 2)) $((1 || 2)) $((0 || 0)) "
                "$((1 ? 10 : 20)) $((0 ? 10 : 20)) "
                "$((0 ? (1 ? 2 : 3) : 4)) $((1 ? 2 : (0 ? 3 : 4))) "
                "$((0 ? ++a : --b)) $((1 ? ++a : --b)) $((0 ? a+b : b)) "
                "$((a = b = 10, a += 2, a -= 1, a *= 2, a /= 3, a %= 5, "
                "a <<= 1, a >>= 1, a &= 7, a ^= 3, a |= 4)) "
                "$((undef + 1)); "
                "for bad in 'rec1' '+' '1+' '2:3' '2**-1' '1/0' '1=2' '1 2' '09' '@'; do "
                '  (eval "echo \\$(($bad))") 2>/dev/null || true; '
                "done",
            ],
        ),
        "42",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                "export A=1 B=2; export A=3; "
                "read -r -d : -n 2 -p prompt -s -t 2 -u 0 v1; "
                "read -d : v1_rest; "
                "read -s -t 20000000 v2; "
                "unset IFS; read u1 u2; "
                "IFS=': '; read a b c; "
                "read d e; "
                "read k1 k2; "
                "read f g; "
                "read; "
                "read '' || true; read '1bad' || true; "
                "read -n -1 || true; "
                "read -t bad || true; "
                "read -u -1 || true; "
                "read -t 0 < /dev/null || true; "
                "ulimit; ulimit -a; ulimit -H -S -c; ulimit -H -c; ulimit -c -d; "
                "ulimit -S -c 0; ulimit -H -c unlimited; ulimit 100000; "
                "ulimit -c bad_num || true; ulimit -z || true",
            ],
            stdin_data=(
                b"ab:line2\nu_one u_two\n  : x : : y  z : \n"
                b"X:Y:\nK1:K2\n\\a\\\nb\x00c\nreply_line\n"
            ),
        )
    )
    assert_ok(
        run_bb(busybox, ["sh", "-c", "X=hello; echo ${X}_world"]),
        "hello_world",
    )

    # Comprehensive hush functional coverage: builtins, expansions, control flow,
    # redirections, functions, job control, interactive pty, and syntax errors.
    hush_profile = work / ".profile"
    hush_profile.write_text("PROFILE_LOADED=yes\n", encoding="utf-8")
    hush_src_file = work / "sourced_script.sh"
    hush_src_file.write_text(
        "echo sourced_arg=$1\n"
        "echo hi 3>/dev/null\n"
        "echo hi >&3 2>/dev/null || true\n"
        "cat </dev/null\n"
        "set -- local_pos1 local_pos2\n"
        "return 0\n"
        "echo unreachable_after_return\n",
        encoding="utf-8",
    )
    hush_path_dir = work / "hush_bin"
    hush_path_dir.mkdir(parents=True, exist_ok=True)
    hush_path_script = hush_path_dir / "path_sourced.sh"
    hush_path_script.write_text("PATH_SOURCED=1\n", encoding="utf-8")
    hush_path_script.chmod(0o755)
    hush_noexec_file = hush_path_dir / "noexec_cmd"
    hush_noexec_file.write_text("#!/bin/sh\necho no\n", encoding="utf-8")
    hush_noexec_file.chmod(0o644)
    hush_main_script = work / "main_script.sh"
    hush_main_script.write_bytes(
        b"# comment line\x00\necho script_arg1=$1\\\n_cont\nv='a\\b'\necho $v\n"
    )
    assert_ok(
        run_bb(busybox, ["hush", str(hush_main_script), "val1"], cwd=work),
        "script_arg1=val1_cont",
    )
    assert run_bb(busybox, ["hush", str(work / "nonexistent_script.sh")]).returncode == 127
    assert run_bb(busybox, ["hush", "-c"]).returncode != 0
    assert run_bb(busybox, ["hush", "-Z"]).returncode != 0
    assert_ok(
        run_bb(
            busybox,
            ["hush", "-s", "s_arg1", "s_arg2"],
            stdin_data=b"echo s_args=$1:$2\n",
        ),
        "s_args=s_arg1:s_arg2",
    )
    assert_ok(
        run_bb(
            busybox,
            ["hush", "-n", "-c", "echo should_not_print"],
        ),
        "",
    )
    assert (
        run_bb(
            busybox,
            ["hush", "-e", "-c", "false; echo should_not_run"],
        ).returncode
        != 0
    )
    assert_ok(
        run_bb(
            busybox,
            ["hush", "-x", "-c", "X=1 echo 'a b' simple; echo hi 11>/dev/null"],
        ),
        "a b simple",
    )
    hush_path_env = f"{hush_path_dir}:{os.environ.get('PATH', '')}"
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                f'HOME="{work}" PATH="{hush_path_env}" '
                "hush -l -c 'echo prof=$PROFILE_LOADED:$0:$1' my0 my1",
            ],
            cwd=work,
        ),
        "prof=yes:my0:my1",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                "trap 'echo exit_trap_top; exit' EXIT; trap 'exit' USR1; kill -USR1 $$",
            ],
        ),
        "exit_trap_top",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                ":; true; false || true; "
                "[ 1 -eq 1 ] && test 'a' = 'a'; "
                "[[ a == a && b != a || ! ( a == b ) ]]; "
                "printf '%s_%d\\n' pf 7; "
                "help >/dev/null; "
                "pwd >/dev/null; "
                "orig_pwd=$(pwd); cd /; cd; HOME='' cd / || true; unset HOME; cd /; "
                f'HOME={work}; cd; cd /nonexistent_dir_xyz 2>/dev/null || true; cd "$orig_pwd"; '
                "eval; eval 'echo eval_single' >/dev/null; eval echo eval_multi 1 >/dev/null; "
                "eval 'echo ok\\' >/dev/null; "
                "echo $(trap) $(trap -p) >/dev/null; "
                "A_EXP=123 B_EXP=$A_EXP C_EXP=$A_EXP true; "
                "A_EMPTY=1 $(true); "
                "exec 7>/dev/null; exec 7>&-; "
                "exec; (exec echo exec_applet >/dev/null); "
                f"(exec {hush_noexec_file}) 2>/dev/null || true; "
                "(exec /nonexistent_bin_xyz) 2>/dev/null || true; "
                "(exit); (exit 0); (exit 5) || echo exit5=$?; (exit 123abc) 2>/dev/null || true; "
                "myfn() { local lv=1; local lv=2; local lv; local lv2; "
                "  lv=$(echo dyn); local lv=3; "
                "  local '1bad=x' 2>/dev/null || true; echo fn_$1_$lv; return 3; }\n"
                "myfn() { local lv=2; echo fn_$1_$lv; return 3; }\n"
                "subfn() ( echo in_subfn; ); subfn; "
                "self_redef() { self_redef() { echo redef_ok; }; }; self_redef; self_redef; "
                "myfn argA || echo fn_rc=$?; "
                "myfn | cat; (myfn) || true; true | gfn() { echo g; }; "
                "{ echo piped_grp; } | cat >/dev/null; "
                "(true && true & wait); "
                "true | ONLY_ASSIGN_PIPE=1 | true; "
                "EMPTY_CMD=''; $EMPTY_CMD ONLY_ASSIGN_VAR=1; "
                "set -x; X_TR=\"a'b\" Y_TR= true '' \"a'b\"; X_ONLY=1; set +x; "
                f"type echo myfn sh {hush_path_script} ./main_script.sh "
                "/nonexistent_slash nonexistent_type_xyz 2>/dev/null || true; "
                "(PATH='::' type main_script.sh) >/dev/null 2>&1 || true; "
                "(unset PATH; type nonexistent_no_path) 2>/dev/null || true; "
                "local outside_fn 2>/dev/null || true; "
                "(return) 2>/dev/null || true; (return bad) 2>/dev/null || true; "
                "umask; umask 0022; umask u=rwx,g=rx,o=rx; "
                "umask 999 2>/dev/null || true; umask +bad 2>/dev/null || true; "
                "export EX_A='val_a' EX_SQ=\"'single'mid'end'\"; export EX_A; export EX_EMPTY; "
                "EX_A=temp_val echo >/dev/null; "
                "export >/dev/null; export -n EX_A=unexp_val EX_A EX_NONEXIST; "
                "(export '1bad=1') 2>/dev/null || true; (export -z) 2>/dev/null || true; "
                "readonly RO_A=ro1; readonly RO_A; readonly RO_EMPTY; readonly >/dev/null; "
                "RO_A=ro_temp echo 2>/dev/null || true; "
                "(RO_A=ro2) 2>/dev/null || true; (export RO_A=ro2) 2>/dev/null || true; "
                "(unset RO_A) 2>/dev/null || true; "
                "unset EX_EMPTY; unset -v EX_SQ; unset -f myfn; unset -f nonexist_fn; "
                "(unset -v -f x) 2>/dev/null || true; (unset -z) 2>/dev/null || true; "
                "set >/dev/null; set -o; set +o; set -o pipefail; set +o pipefail; "
                "(set -o bad_opt) 2>/dev/null || true; (set -z) 2>/dev/null || true; "
                "set -x +x -n +n -e +e; set -- p1 p2 p3; set p4 p5 p6; "
                "shift; shift 2; shift 99 2>/dev/null || true; (shift bad) 2>/dev/null || true; "
                "set --; "
                "OPTIND=1; while getopts ':a:bc' opt -a aval -b -c -z rest; do "
                "  echo opt=$opt:$OPTARG; "
                "done; "
                "OPTIND=1; OPTERR=1 getopts 'a:b' opt -b -z 2>/dev/null || true; "
                "OPTIND=1; OPTERR=0 getopts 'a:b' opt -z 2>/dev/null || true; "
                "OPTIND=1; set -- -a; getopts 'a' opt; "
                "(getopts) 2>/dev/null || true; (getopts ':a') 2>/dev/null || true; "
                f"(. ) 2>/dev/null || true; . {hush_src_file} src_arg1; "
                f". {hush_src_file}; . path_sourced.sh; "
                "(. nonexistent_source.sh) 2>/dev/null || true; "
                "(. ./nonexistent_rel.sh) 2>/dev/null || true; "
                "times >/dev/null; memleak || true; memleak || true",
            ],
            cwd=work,
        ),
        "exit5=5",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                # Expansions: $'...', ${var:...}, ${var/...}, brace expansion, backticks, math
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                "s=$'\\a\\b\\e\\f\\n\\r\\t\\v\\\\\\'\\\"\\x41\\x0z\\x\\101\\19\\cA\\z'; "
                "emp_sq=$''; nul_sq=$'\\0'; ctl_sq=$'\x03'; "
                'echo "" a#b \x03 "\x03" \'\x03\' \\\x03 >/dev/null; '
                'dq="$\'hi\' \\$ \\` \\" \\\\ \\a \\\n`echo dq_bt`"; '
                "u=''; v='abcdef'; g='*?[\\\\'; br_v='{a,b}'; "
                'echo "$br_v"; '
                "unset un_v; echo ${un_v/a/b} ${un_v:0:1}; "
                "set -- 1 2 3 4 5 6 7 8 9 10; echo ${10} ${1:-x}; set --; "
                'echo $* $@ "$*" "$@"; '
                'x_e=; echo ${x_e:-"$@"}; '
                'qfn() { for qi; do echo "|$qi|"; done; }; '
                'x_q=x; set -- a b; qfn ${x_q:+"$@"}; '
                "ifs_v='a '; echo $ifs_v$ifs_v ${ifs_v}b; "
                "IFS=': '; colon_v='a:b :c'; echo $colon_v; unset IFS; "
                "echo ${#v} ${#} ${#@} ${#*}; "
                "echo ${u:-def1} ${u-def2} ${v:-def3} ${v:+alt1} ${u:+alt2} ${u+alt3}; "
                "echo ${u:-'sq' \"dq\" $v `echo bt` \\\\a a b}; "
                'echo "${u:-\'sq\' "dq" $v `echo bt` \\\\a a b}"; '
                "echo ${u:=asgn1} $u; u=''; "
                "echo ${u:='sq' \"dq\" $'\\x41' $v `echo bt` \\\\a * ? [}; "
                'echo "${v#"$g"}" "${v##*a}" "${v%f*}" "${v%%d*}"; '
                "echo ${v#'a'} ${v%\"f\"} ${v#$'a'} ${v%`echo f`} ${v#\\\\a}; "
                "(echo ${empty_req:?custom_err}) 2>/dev/null || true; "
                "(echo ${empty_req?}) 2>/dev/null || true; "
                "(echo ${1:=bad_assign}) 2>/dev/null || true; "
                "echo ${v:1:3} ${v: -3:2} ${v:1:-1} ${v:0:0} ${v:10:2} ${v:1}; "
                "echo ${v:(0):1}; "
                "(echo ${v:'0':\"1\"`echo`}) 2>/dev/null || true; "
                "(echo ${v:\\0:1}) 2>/dev/null || true; "
                "echo $(( (1 + 2) * 3 )) $((-5)); "
                "(echo ${v:1:-99}) 2>/dev/null || true; "
                "(echo ${v:} ${v:1:} ${v:bad+} ${v:1:bad+}) 2>/dev/null || true; "
                "echo ${#:-5} ${#:0:1}; "
                "p='aabbaabb'; sl='/dev/ram'; emp_pat=''; "
                "echo ${sl////-} ${sl///r/-} ${p/$emp_pat/X} ${p/a*b/X} ${p/*z/X} ${p/z*/X}; "
                "echo ${p/a/X} ${p//a/X} ${p/#aa/Y} ${p/%bb/Z} ${p/a} ${p//b/} ${u/a/X}; "
                'echo "${p/a/X}" "${g/\\*/X}"; '
                "echo ${\\\nv} [a-z] {\\a,b} {a {a,b "
                "{x,y}{1,2} {solo} \\{no,brace\\} {a,{b,c}} ''; "
                "bt=`echo \\`echo nested_bt\\``; echo $bt; "
                "long_sub=$(seq 1 2000); "
                "echo $((10 + 5)) $((2 * 3)); "
                "(echo $((1 / 0))) 2>/dev/null || true; "
                'set -- a b; echo $* $@ "$*" "$@"; IFS=\'\'; echo "$*"; unset IFS; '
                'echo $$ $! $? $- $# $* $@ "$*" "$@" '
                "$_ $PPID $HOSTNAME $EPOCHSECONDS $EPOCHREALTIME",
            ],
            cwd=work,
        ),
        "nested_bt",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "hush",
                "-c",
                # Control flow, loops, break/continue, case, redirections, heredocs, traps, jobs
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                "if false; then echo no; elif true; then echo elif_ok; else echo no2; fi; "
                "if false; then echo no; else echo else_ok; fi; "
                "if { true; } then { true; } fi; "
                "case if in if) echo case_kw_ok ;; esac; "
                "for x in a b c; do "
                "  for y in 1 2; do "
                "    if [ $x = b ]; then continue 2; fi; "
                "    if [ $x = c ]; then break 2; fi; "
                "  done; "
                "done; "
                "for x in 1 2; do break 99; done; "
                "(for x in 1 2; do break bad; done) 2>/dev/null || true; "
                "(for x in 1 2; do break 1 extra; done) 2>/dev/null || true; "
                "break 2>/dev/null; continue 2>/dev/null; "
                "set -- arg_loop; for x; do echo $x >/dev/null; done; "
                "for x in; do echo $x; done; "
                "i=0; while [ $i -lt 2 ]; do i=$((i + 1)); done; "
                "until [ $i -ge 4 ]; do i=$((i + 1)); done; "
                "case hello in (no|nah) echo bad ;; h*o) echo case_ok ;; *) echo def ;; esac; "
                "false; case nomatch in y) echo bad ;; esac; "
                "for x in a\ndo :; done\n"
                "{ echo grp_out; } >|grp.txt; "
                "{ { echo nested_grp; } } >>grp.txt; (echo sub_out) >>grp.txt; "
                "cat <grp.txt <>grp.txt &>>grp.txt &>grp2.txt; "
                "cat <<<'herestr_val' >/dev/null; "
                "cat <<-EOF >/dev/null\n\tindented_heredoc\\\n\t_cont\\\\\n\tEOF\n"
                "cat <<'EOF' >/dev/null\n$literal_heredoc\nEOF\n"
                "big_hd=$(seq 1 20000); cat <<EOF >/dev/null\n$big_hd\nEOF\n"
                "echo hi 3>sq3.txt 4>sq4.txt 3>&- 3>sq3b.txt 1>&2 2>&1 9>&- 2>/dev/null; "
                "echo hi 2>/dev/null 10>/dev/null; "
                "echo hi >&$empty_bad 2>/dev/null || true; "
                "(echo hi 2>/dev/null >&10) 2>/dev/null || true; "
                "(echo FOO >&2 | echo BAR 3>/nonexistent_dir_xyz/file) 2>/dev/null || true; "
                "(echo hi >&9999) 2>/dev/null || true; "
                "(echo hi </nonexistent_redir_file) 2>/dev/null || true; "
                "echo </nonexistent_redir_file 2>/dev/null || true; "
                "! true || echo bang_true; ! false && echo bang_false; ! ! true; "
                "set -o pipefail; false | true || echo pipefail_caught; set +o pipefail; "
                "true | >null_cmd_redir.txt; "
                # Traps & signals
                "(trap 'echo exit_trap_ran; exit' EXIT; false) >/dev/null || true; "
                "trap; trap 'echo trap_usr1; return 42' USR1; trap '' USR2; "
                "tfn() { kill -USR1 $$; echo not_reached; }; tfn || echo tfn_rc=$?; "
                "trap -- 'echo trap_int' INT; trap 'echo trap_exit' 0; "
                "sub_trap=$(echo in_sub_with_traps); "
                "trap; kill -USR1 $$; echo after_usr1=$?; "
                "trap - USR1 USR2 INT; trap 0; "
                "(trap -bad) 2>/dev/null || true; "
                "trap 'echo x' BAD_SIG_NAME 2>/dev/null || true; "
                "trap only_one 2>/dev/null || true; "
                # Background jobs, wait, kill, jobs, fg, bg
                "(exit 0) & usleep 50000 | cat; wait; "
                "sleep 1 & p1=$!; sleep 1 & p2=$!; "
                "kill $p1 $p2 2>/dev/null || true; wait $p2 $p1 2>/dev/null || true; "
                "sleep 0.1 & bg_pid=$!; jobs >/dev/null; wait $bg_pid; wait $bg_pid; "
                "(exit 7) & bg2=$!; sleep 0.1; wait $bg2 || echo wait_exited=$?; "
                "sleep 0.1 & wait -n; wait -n 2>/dev/null || true; "
                "sleep 0.1 & wait %1; "
                "sleep 0.1 & wait; "
                "wait 999999 2>/dev/null || true; "
                "wait %99 2>/dev/null || true; "
                "wait %% 2>/dev/null || true; "
                "wait %bad 2>/dev/null || true; "
                "wait not_a_pid 2>/dev/null || true; "
                "sleep 1 & kill %1; wait 2>/dev/null || true; "
                "true | sleep 1 & sleep 0.05; "
                "kill %1 2>/dev/null || true; wait 2>/dev/null || true; "
                "kill %99 2>/dev/null || true; kill -l >/dev/null; "
                "fg 2>/dev/null || true; bg 2>/dev/null || true",
            ],
            cwd=work,
        ),
        "case_ok",
    )

    # Syntax errors in hush
    for bad_hush_syntax in (
        "'unterminated single",
        '"unterminated double',
        "`unterminated backtick",
        "$'unterminated dollar sq",
        "${unterminated_var",
        "${v\\\n}",
        "${1bad}",
        "${1234567890}",
        "${v:'unterm}",
        '${v:"unterm}',
        '${v:"\\`unterm"}',
        "${v:`unterm}",
        "${v:(unterm}",
        "${v:\\",
        "$(unterminated cmdsub",
        "$((1 + 2)",
        "(unterminated subshell",
        "{ unterminated group",
        "if true; then echo hi",
        "elif true; then :; fi",
        "else echo hi; fi",
        "fi",
        "do echo hi; done",
        "done",
        "esac",
        "for 1bad in a; do :; done",
        "for x in a; echo missing_do; done",
        "for x in a\nb; do :; done",
        "case x in a) echo hi",
        "case x in a",
        "case x not_in a) :;; esac",
        "| cat",
        "&& true",
        "|| true",
        "echo hi |",
        "echo hi | && true",
        "echo hi | || true",
        "echo hi | &",
        "()",
        "{ }",
        "{echo a; }",
        "{ true | }",
        ")",
        "}",
        "f() echo bad_func_body",
        "f( ) { :; } extra_after_func",
        "a b () { :; }",
        "a (b) { :; }",
        "{ echo a; }bad",
        "true | ! true",
        "echo >",
        "echo >>",
        "echo <",
        "echo <<EOF",
        "echo <<EOF\nunterminated_heredoc",
        "echo >&-",
        "[[ a == ]]",
        "[[ a",
        "[[ ( a == a ]]",
    ):
        assert run_bb(busybox, ["hush", "-c", bad_hush_syntax]).returncode != 0

    assert (
        run_bb(
            busybox,
            ["hush", "-s"],
            stdin_data=b"true | ;\n",
        ).returncode
        != 0
    )

    assert_ok(
        run_bb(
            busybox,
            ["hush", "-i"],
            stdin_data=(
                b"echo pty_interactive_ok\\\n_line2\n"
                b"set -e -x; echo $-; set +e +x\n"
                b". /nonexistent_pty_source\n"
                b"echo ${1:=bad}\n"
                b"cat </dev/null\n"
                b"(exec true)\n"
                b"echo hi >&255\n"
                b"echo hi 255>/dev/null\n"
                b"sleep 1 &\n"
                b"kill %1\n"
                b"wait\n"
                b"sleep 1 &\n"
                b"jobs\n"
                b"bg %1\n"
                b"fg %1\n"
                b"fg\n"
                b"fg %99\n"
                b"fg %%\n"
                b"fg %bad\n"
                b"syntax_err_line ( extra_discarded\n"
                b"exit 0\n"
            ),
            cwd=work,
        ),
        "pty_interactive_ok_line2",
    )

    # Comprehensive ash functional coverage: builtins, aliases, expansions,
    # control flow, AST copyfunc, heredocs, redirections, traps, jobs, and interactive mode.
    ash_env_file = work / "ash_env.sh"
    ash_env_file.write_text("ASH_ENV_LOADED=1\n", encoding="utf-8")
    assert_ok(
        run_bb(busybox, ["ash", str(hush_main_script), "val1"], cwd=work),
        "script_arg1=val1_cont",
    )
    assert run_bb(busybox, ["ash", str(work / "nonexistent_script.sh")]).returncode != 0
    assert run_bb(busybox, ["ash", "-c"]).returncode != 0
    assert run_bb(busybox, ["ash", "-Z"]).returncode != 0
    assert run_bb(busybox, ["ash", "+Z"]).returncode != 0
    assert_ok(run_bb(busybox, ["ash", "-o", "-c", ":"]))
    assert_ok(run_bb(busybox, ["ash", "+o", "-c", ":"]))
    assert b"illegal option" in run_bb(busybox, ["ash", "-o", "bad_opt_xyz", "-c", ":"]).stderr
    assert b"illegal option" in run_bb(busybox, ["ash", "+o", "bad_opt_xyz", "-c", ":"]).stderr
    assert_ok(
        run_bb(
            busybox,
            ["ash", "-s", "s_arg1", "s_arg2"],
            stdin_data=b"echo s_args=$1:$2\n",
        ),
        "s_args=s_arg1:s_arg2",
    )
    assert_ok(
        run_bb(
            busybox,
            ["ash", "-n", "-c", "echo should_not_print"],
        ),
        "",
    )
    assert (
        run_bb(
            busybox,
            ["ash", "-e", "-c", "false; echo should_not_run"],
        ).returncode
        != 0
    )
    assert_ok(
        run_bb(
            busybox,
            ["ash", "-v", "-x", "-c", "X=1 echo 'a b' simple; echo hi 11>/dev/null"],
        ),
        "a b simple",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                f'HOME="{work}" ENV="{ash_env_file}" PATH="{hush_path_env}" '
                f"\"{busybox.resolve()}\" ash -l -c 'echo prof=$PROFILE_LOADED:$0:$1' my0 my1",
            ],
            cwd=work,
        ),
        "prof=yes:my0:my1",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                "export A=1 B=2; export A=3; "
                "read -r -d : -n 2 -p prompt -s -t 2 -u 0 v1; "
                "read -d : v1_rest; "
                "read -s -t 20000000 v2; "
                "unset IFS; read u1 u2; "
                "IFS=': '; read a b c; "
                "read d e; "
                "read k1 k2; "
                "read f g; "
                "read; "
                "read '' 2>/dev/null || true; (read '1bad') 2>/dev/null || true; "
                "(read -n -1) 2>/dev/null || true; "
                "(read -t bad) 2>/dev/null || true; "
                "(read -u -1) 2>/dev/null || true; "
                "(read -z) 2>/dev/null || true; "
                "read -t 0 < /dev/null || true; "
                "ulimit; ulimit -a; ulimit -H -S -c; ulimit -H -c; ulimit -c -d; "
                "ulimit -S -c 0; ulimit -H -c unlimited; ulimit 100000; "
                "(ulimit -c bad_num) 2>/dev/null || true; (ulimit -z) 2>/dev/null || true",
            ],
            stdin_data=(
                b"ab:line2\nu_one u_two\n  : x : : y  z : \n"
                b"X:Y:\nK1:K2\n\\a\\\nb\x00c\nreply_line\n"
            ),
        )
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                ":; true; false || true; "
                "[ 1 -eq 1 ] && test 'a' = 'a'; "
                "[[ a = a ]]; "
                "printf '%s_%d\\n' pf 7; "
                "pwd >/dev/null; pwd -L >/dev/null; pwd -P >/dev/null; "
                "orig_pwd=$(pwd); cd /; cd -L /; cd -P /; cd - >/dev/null; cd; "
                f"CDPATH=.:{work} cd grp_test >/dev/null; "
                f"PATH={hush_path_dir}%builtin:{hush_path_dir}%func:{hush_path_dir}%bad:$PATH "
                "type path_sourced.sh >/dev/null 2>&1 || true; "
                "HOME='' cd / 2>/dev/null || true; unset HOME; (cd) 2>/dev/null || true; "
                f'HOME="{work}"; cd; (cd /nonexistent_dir_xyz) 2>/dev/null || true; '
                'cd -- "$orig_pwd"; '
                # Aliases
                "alias al1='echo al_one ' al2=\"echo 'sq'\"\n"
                "alias al1='echo al_updated '\n"
                "alias; alias al1; (alias not_an_alias) 2>/dev/null || true\n"
                "al1 al2\n"
                "alias self_un='unalias self_un; echo self_un_ok'\n"
                "self_un\n"
                "alias self_rm='unalias -a; echo self_rm_ok'\n"
                "self_rm\n"
                "alias a_keep='echo keep'; unalias a_keep; "
                "(unalias not_found_alias) 2>/dev/null || true; unalias -a; "
                # Hash & command lookup
                "hash -r; hash echo ls cat 2>/dev/null || true; hash >/dev/null; "
                "(hash nonexistent_cmd_xyz) 2>/dev/null || true; "
                "hash -v >/dev/null 2>&1 || true; "
                # Let builtin
                "let 'la = 2 + 3' 'lb = la * 2'; ! let '0'; (let) 2>/dev/null || true; "
                # Eval, exec, exit
                "eval; eval 'echo eval_single' >/dev/null; eval echo eval_multi 1 >/dev/null; "
                "exec 7>/dev/null; exec 7>&-; "
                "exec; (exec -a custom_arg0 /bin/echo exec_applet >/dev/null); "
                f"(exec {hush_noexec_file}) 2>/dev/null || true; "
                "(exec /nonexistent_bin_xyz) 2>/dev/null || true; "
                "(exit); (exit 0); (exit 5) || echo exit5=$?; (exit 123abc) 2>/dev/null || true; "
                # Complex AST function to exercise calcsize / copynode / sizenodelist / copynodelist
                "complex_ast_fn() {\n"
                "  local lv=1 lv_unset; local -; set -x; set +x;\n"
                "  LV_A=1 echo cmd_with_redir >/dev/null 2>&1;\n"
                "  echo pipe1 | cat >/dev/null;\n"
                "  { echo redir_blk; } >/dev/null;\n"
                "  (echo subshell_blk >/dev/null);\n"
                "  true && false || true;\n"
                "  ! false;\n"
                "  if true; then :; elif false; then :; else :; fi;\n"
                "  while false; do :; done;\n"
                "  until true; do :; done;\n"
                "  for fx in 1 2; do :; done;\n"
                "  case x in a|b) : ;; *) : ;; esac;\n"
                "  inner_fn() { :; };\n"
                "  cat <<EOF >/dev/null\n"
                "heredoc_in_fn $lv $(echo sub_in_fn)\n"
                "EOF\n"
                "  (true & wait);\n"
                "  return 3;\n"
                "}\n"
                "complex_ast_fn argA || echo fn_rc=$?; "
                "complex_ast_fn() { echo replaced_fn; }; complex_ast_fn >/dev/null; "
                "self_redef() { self_redef() { echo redef_ok; }; }; self_redef; self_redef; "
                "(local outside_fn) 2>/dev/null || true; "
                "(return) 2>/dev/null || true; (return bad) 2>/dev/null || true; "
                # Umask
                "umask; umask -S; umask 0022; umask -S; "
                "umask u=rwx,g=rx,o=rx; umask ug+w,o-rx,a+X; umask u=u,g=u-w,o=g; "
                "(umask 999) 2>/dev/null || true; (umask +bad) 2>/dev/null || true; "
                # Export, readonly, unset, set, shift, getopts, dot, times
                "export EX_A='val_a' EX_SQ=\"'single'mid'end'\" EX_SP='a b!;#`[\\~'; "
                "export EX_A; export EX_EMPTY; "
                "EX_A=temp_val echo >/dev/null; "
                "export >/dev/null; export -p >/dev/null; "
                "export -n EX_A=unexp_val EX_A EX_NONEXIST 2>/dev/null || true; "
                "(export '1bad=1') 2>/dev/null || true; (export -z) 2>/dev/null || true; "
                "readonly RO_A=ro1; readonly RO_A; readonly RO_EMPTY; readonly >/dev/null; "
                "readonly -p >/dev/null; "
                "(RO_A=ro_temp echo) 2>/dev/null || true; "
                "(RO_A=ro2) 2>/dev/null || true; (export RO_A=ro2) 2>/dev/null || true; "
                "(unset RO_A) 2>/dev/null || true; "
                "unset EX_EMPTY; unset -v EX_SQ; unset -f complex_ast_fn; unset -f nonexist_fn; "
                "(unset -z) 2>/dev/null || true; "
                "set >/dev/null; set -o >/dev/null; set +o >/dev/null; "
                "set -o pipefail; set +o pipefail; set -o errexit; set +o errexit; "
                "(set -o bad_opt) 2>/dev/null || true; (set +o bad_opt) 2>/dev/null || true; "
                "(set -z) 2>/dev/null || true; (set +z) 2>/dev/null || true; "
                "set -x +x -v +v -f +f -u +u -a +a -b +b -C +C -E +E -I +I; "
                "set -- p1 p2 p3; set p4 p5 p6; set -- a b; set -- c d; "
                "shift; shift 1; (shift 99) 2>/dev/null || true; (shift bad) 2>/dev/null || true; "
                "set --; "
                f"(. ) 2>/dev/null || true; . {hush_src_file} src_arg1; "
                f". {hush_src_file}; . path_sourced.sh; "
                "(. nonexistent_source.sh) 2>/dev/null || true; "
                "(. ./nonexistent_rel.sh) 2>/dev/null || true; "
                "command -v echo sh path_sourced.sh nonexistent_xyz >/dev/null 2>&1 || true; "
                "command -V echo sh path_sourced.sh nonexistent_xyz >/dev/null 2>&1 || true; "
                "command -p echo ok >/dev/null; "
                "type echo sh path_sourced.sh ./main_script.sh "
                "nonexistent_xyz >/dev/null 2>&1 || true; "
                "times >/dev/null",
            ],
            cwd=work,
        ),
        "exit5=5",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                # Expansions, tilde, $'...', ${var...}, globs, backticks, math
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                "echo ~ ~/sub ~root ~nonexistent_user_xyz_999 >/dev/null; "
                "t_var=~:~root/dir; echo $t_var >/dev/null; "
                "s=$'\\a\\b\\e\\f\\n\\r\\t\\v\\\\\\'\\\"\\x41"
                "\\x0z\\x\\101\\19\\cA\\u0041\\U00000042\\z'; "
                "emp_sq=$''; nul_sq=$'\\0'; ctl_sq=$'\x03'; "
                'echo "" a#b \x03 "\x03" \'\x03\' \\\x03 >/dev/null; '
                'dq="$\'hi\' \\$ \\` \\" \\\\ \\a \\\n`echo dq_bt`"; '
                "u=''; v='abcdef'; g='*?[\\\\'; "
                "unset un_v; echo ${un_v/a/b} ${un_v:0:1}; "
                "set -- 1 2 3 4 5 6 7 8 9 10; echo ${10} ${1:-x}; set --; "
                'echo $* $@ "$*" "$@"; '
                'x_e=; echo ${x_e:-"$@"}; '
                'qfn() { for qi; do echo "|$qi|"; done; }; '
                'x_q=x; set -- a b; qfn ${x_q:+"$@"}; '
                "ifs_v='a '; echo $ifs_v$ifs_v ${ifs_v}b; "
                "IFS=': '; colon_v='a:b :c'; echo $colon_v; unset IFS; "
                "echo ${#v} ${#} ${#@} ${#*}; "
                "echo ${u:-def1} ${u-def2} ${v:-def3} ${v:+alt1} ${u:+alt2} ${u+alt3}; "
                "echo ${u:=asgn1} $u; u=''; "
                'echo "${v#"$g"}" "${v##*a}" "${v%f*}" "${v%%d*}" "${v#a}" "${v%f}"; '
                "esc_v='a*b?c[d]'; "
                "echo ${esc_v#a\\*} ${esc_v##a\\*} ${esc_v%\\[d\\]} ${esc_v%%\\?*}; "
                "(echo ${empty_req:?custom_err}) 2>/dev/null || true; "
                "(echo ${empty_req?}) 2>/dev/null || true; "
                "(echo ${un_v:?}) 2>/dev/null || true; "
                "(echo ${un_v?}) 2>/dev/null || true; "
                "(echo ${un_v?custom_un}) 2>/dev/null || true; "
                "(echo ${1:=bad_assign}) 2>/dev/null || true; "
                "echo ${v:1:3} ${v: -3:2} ${v:1:-1} ${v:0:0} "
                "${v:10:2} ${v:1} ${v:0:0x100000001} ${v: -0x100000001:1}; "
                "set -- a b c d; echo ${@:2:2} ${*:2:2} ${@: -2:1}; "
                "(echo ${v:1:-99}) 2>/dev/null || true; "
                "p='aabbaabb'; sl='/dev/ram'; emp_pat=''; "
                "echo ${sl////-} ${sl///r/-} ${p/$emp_pat/X} ${p/a*b/X} ${p/*z/X} ${p/z*/X}; "
                "echo ${p/a/X} ${p//a/X} ${p/#aa/Y} ${p/%bb/Z} ${p/a} ${p//b/} ${u/a/X}; "
                'echo "${p/a/X}" "${g/\\*/X}"; '
                # Globbing with multiple files and subdirectories to exercise expmeta & msort
                "mkdir -p glob_d/sub1 glob_d/sub2; "
                "touch glob_d/c.txt glob_d/a.txt glob_d/b.txt glob_d/d.txt glob_d/e.txt "
                "glob_d/.hidden glob_d/sub1/x.txt glob_d/sub2/y.txt; "
                "echo glob_d/*.txt glob_d/?.txt glob_d/[a-c].txt glob_d/[!a].txt "
                "glob_d/.* glob_d/sub*/*.txt glob_d/nonmatch_* >/dev/null; "
                "bt=`echo \\`echo nested_bt\\``; echo $bt; "
                "long_sub=$(seq 1 2000); "
                'set -- a b; echo $* $@ "$*" "$@"; IFS=\'\'; echo "$*"; unset IFS; '
                'echo $$ $! $? $- $# $* $@ "$*" "$@" '
                "$LINENO $FUNCNAME $RANDOM $SECONDS $EPOCHSECONDS $EPOCHREALTIME",
            ],
            cwd=work,
        ),
        "nested_bt",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                # Control flow, loops, break/continue, case, redirections, heredocs, traps, jobs
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                "if false; then echo no; elif true; then echo elif_ok; else echo no2; fi; "
                "if false; then echo no; else echo else_ok; fi; "
                "for x in a b c; do "
                "  for y in 1 2; do "
                "    if [ $x = b ]; then continue 2; fi; "
                "    if [ $x = c ]; then break 2; fi; "
                "  done; "
                "done; "
                "for x in 1 2; do break 99; done; "
                "(for x in 1 2; do break 0; done) 2>/dev/null || true; "
                "(for x in 1 2; do break bad; done) 2>/dev/null || true; "
                "break 2>/dev/null || true; continue 2>/dev/null || true; "
                "set -- arg_loop; for x; do echo $x >/dev/null; done; "
                "for x in; do echo $x; done; "
                "i=0; while [ $i -lt 2 ]; do i=$((i + 1)); done; "
                "until [ $i -ge 4 ]; do i=$((i + 1)); done; "
                "case hello in (no|nah) echo bad ;; h*o) echo case_ok ;; *) echo def ;; esac; "
                "false; case nomatch in y) echo bad ;; esac; "
                "{ echo grp_out; } >|grp_ash.txt; "
                "{ { echo nested_grp; } } >>grp_ash.txt; (echo sub_out) >>grp_ash.txt; "
                "cat <grp_ash.txt <>grp_ash.txt >&grp_ash.txt &>grp_ash2.txt; "
                "(echo hi 2>&grp_ash.txt) 2>/dev/null || true; "
                "cat <<<'herestr_val' >/dev/null; "
                "cat <<-EOF >/dev/null\n\tindented_heredoc\\\n\t_cont\\\\\n\tEOF\n"
                "cat <<'EOF' >/dev/null\n$literal_heredoc\nEOF\n"
                "big_hd=$(seq 1 20000); cat <<EOF >/dev/null\n$big_hd\nEOF\n"
                "echo hi 3>sq3.txt 4>sq4.txt 3>&- 3>sq3b.txt 1>&2 2>&1 9>&- 2>/dev/null; "
                "echo hi 2>/dev/null 10>/dev/null; "
                "(echo hi >&$empty_bad) 2>/dev/null || true; "
                "(echo hi 2>/dev/null >&10) 2>/dev/null || true; "
                "(echo hi >&9999) 2>/dev/null || true; "
                "(echo hi </nonexistent_redir_file) 2>/dev/null || true; "
                "(echo hi >/nonexistent_dir_xyz/file) 2>/dev/null || true; "
                "! true || echo bang_true; ! false && echo bang_false; "
                "set -o pipefail; false | true || echo pipefail_caught; set +o pipefail; "
                # Traps & ERR trap
                "(trap 'echo exit_trap_ran; exit' EXIT; false) >/dev/null || true; "
                "set -E; trap 'echo err_trap' ERR; false; trap - ERR; set +E; "
                "trap; "
                "trap 'echo trap_usr1; return 42' USR1; trap '' USR2; "
                "tfn() { kill -USR1 $$; }; tfn || echo tfn_rc=$?; "
                "trap 'echo trap_usr1_plain' USR1; "
                "trap -- 'echo trap_int' INT; trap 'echo trap_exit' EXIT; "
                "sub_trap=$(trap); "
                "trap; kill -USR1 $$; "
                "trap - USR1 USR2 EXIT; trap 2; "
                "(trap -bad) 2>/dev/null || true; "
                "(trap 'echo x' BAD_SIG_NAME) 2>/dev/null || true; "
                # Background jobs, wait, getjob
                "(exit 0) & usleep 50000 | cat; wait; "
                "sleep 1 & p1=$!; sleep 1 & p2=$!; "
                "kill $p1 $p2 2>/dev/null || true; wait $p2 $p1 2>/dev/null || true; "
                "sleep 0.1 & bg_pid=$!; wait $bg_pid; wait $bg_pid; "
                "(exit 7) & bg2=$!; sleep 0.1; wait $bg2 || echo wait_exited=$?; "
                "sleep 0.1 & wait %1; "
                "sleep 0.1 & wait %%; "
                "sleep 0.1 & wait %+; "
                "sleep 0.1 & sleep 0.1 & wait %- 2>/dev/null || true; wait; "
                "sleep 0.1 & wait %sleep; "
                "sleep 0.1 & wait %?1; "
                "wait 999999 2>/dev/null || true; "
                "(wait %99) 2>/dev/null || true; "
                "(wait %%) 2>/dev/null || true; "
                "(wait %bad) 2>/dev/null || true; "
                "(wait %?nonexistent) 2>/dev/null || true; "
                "(wait not_a_pid) 2>/dev/null || true; "
                "wait",
            ],
            cwd=work,
        ),
        "case_ok",
    )

    # Syntax errors in ash
    for bad_ash_syntax in (
        "'unterminated single",
        '"unterminated double',
        "`unterminated backtick",
        "$'unterminated dollar sq",
        "${unterminated_var",
        "${1bad}",
        "$(unterminated cmdsub",
        "$((1 + 2)",
        "(unterminated subshell",
        "{ unterminated group",
        "if true; then echo hi",
        "elif true; then :; fi",
        "else echo hi; fi",
        "fi",
        "do echo hi; done",
        "done",
        "esac",
        "for 1bad in a; do :; done",
        "for x in a; echo missing_do; done",
        "case x in a) echo hi",
        "case x in a",
        "case x not_in a) :;; esac",
        "| cat",
        "&& true",
        "|| true",
        "echo hi |",
        "echo hi | && true",
        "()",
        "{ }",
        ")",
        "}",
        "f() ;",
        "a=b() { :; }",
        "function () { :; }",
        "f(a) { :; }",
        "echo >",
        "echo >>",
        "echo <",
        "echo <<",
        ";;",
        "function fn_bad syntax_err",
        "if true; echo missing_then; fi",
        "if false; then :; elif true; echo missing_elif_then; fi",
        "while true; echo missing_do; done",
        "for x in a b & do :; done",
        "case ; in a) :;; esac",
        "echo ${}",
        "echo ${^}",
        "echo ${#^}",
        "echo ${#1a}",
        "echo ${1+a\\}",
    ):
        assert run_bb(busybox, ["ash", "-c", bad_ash_syntax]).returncode != 0
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                "function fn_kw1() { :; }; function fn_kw2 { :; }; f_simple() echo ok; "
                "cat <<EOF\nunterminated_heredoc",
            ],
        )
    )

    assert_ok(
        run_bb(
            busybox,
            ["ash", "-i"],
            stdin_data=(
                b"PS1='prompt_${PWD##*/}\\$ '\n"
                b"echo ash_interactive_ok\\\n_line2\n"
                b"set -e -x; echo $-; set +e +x\n"
                b"exit 0\n"
            ),
            cwd=work,
        ),
        "ash_interactive_ok_line2",
    )
    run_bb(
        busybox,
        ["ash", "-i"],
        stdin_data=b"syntax_err_line ( extra_discarded\n",
        cwd=work,
    )

    # Additional ash coverage: --login, bad long opt, -i + ENV, PTY interactive,
    # NOEXEC standalone applet exec, ENOEXEC script fallback, %builtin/%func PATH,
    # describe_command (type/command -v/-V), CDPATH dot/dotdot, BASH_XTRACEFD,
    # heredoc edge cases, local duplicate/unset, read timeout, and IFS/glob edges.
    ash_no_shebang = work / "ash_no_shebang.sh"
    ash_no_shebang.write_text("echo enoexec_script_ok:$1\n", encoding="utf-8")
    ash_no_shebang.chmod(0o755)
    ash_func_dir = work / "ash_func_dir"
    ash_func_dir.mkdir(exist_ok=True)
    (ash_func_dir / "ash_autoload_fn").write_text(
        "ash_autoload_fn() { echo autoload_fn_ok; }\n",
        encoding="utf-8",
    )
    (ash_func_dir / "ash_bad_autoload").write_text(
        "echo not_defining_func\n",
        encoding="utf-8",
    )
    ash_redir_src = work / "ash_redir_src.sh"
    ash_redir_src.write_text(
        "exec 10>/dev/null\nexec 10>&-\necho redir_src_ok\n",
        encoding="utf-8",
    )
    ash_long_script = work / "ash_long_script.sh"
    ash_long_script.write_bytes(
        b"set -v\necho " + (b"x" * 2500) + b" >/dev/null\nset +v\necho long_script_ok"
    )
    assert_ok(
        run_bb(
            busybox,
            ["ash", str(ash_long_script)],
            cwd=work,
        ),
        "long_script_ok",
    )
    assert_ok(
        run_bb(
            busybox,
            ["env", "-i", str(busybox.resolve()), "ash", "-c", "echo shlvl_init=$SHLVL"],
            cwd=work,
        ),
        "shlvl_init=1",
    )
    assert_ok(
        run_bb(
            busybox,
            ["ash", "--login", "-c", "echo login_long_ok"],
            cwd=work,
        ),
        "login_long_ok",
    )
    assert run_bb(busybox, ["ash", "--bad-long-opt", "-c", ":"]).returncode != 0
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                f'HOME="{work}" ENV="{ash_env_file}" '
                f"\"{busybox.resolve()}\" ash -i -c 'echo env_loaded=$ASH_ENV_LOADED'",
            ],
            cwd=work,
        ),
        "env_loaded=1",
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                "(exec echo noexec_standalone_ok) 2>/dev/null || true; "
                f'(exec "{busybox.resolve()}" ash -c "echo reexec_ok")',
            ],
            cwd=work,
        ),
        "reexec_ok",
    )
    run_bb(
        busybox,
        ["ash", "-c", f'"{ash_no_shebang}" arg_one'],
        cwd=work,
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "ash",
                "-c",
                f'export HOME="{work}" PATH="{hush_path_env}"; '
                # CDPATH dot / dotdot / triple-dot
                "mkdir -p ...; cd . >/dev/null; cd .. >/dev/null; "
                f'cd "{work}"; CDPATH=. cd ... >/dev/null; cd "{work}"; '
                "cd // >/dev/null; cd /// >/dev/null; "
                f'cd -P "{work}"; pwd -P >/dev/null; '
                # local duplicate and unset, and local on existing outer variable
                "outer_v=0; loc_fn() { local outer_v; local x=1; local x=2; "
                "local x; local y; echo loc=$x:$y:$outer_v; }; loc_fn; "
                # unalias while alias is in use, plus multiple aliases in same bucket
                "alias self_unal='unalias self_unal; echo self_unal_ok'\n"
                "self_unal\n"
                "alias a1=':' a2=':' a3=':' a4=':' a5=':' a6=':' a7=':' a8=':' a9=':' a10=':' "
                "a11=':' a12=':' a13=':' a14=':' a15=':' a16=':' a17=':' a18=':' a19=':' a20=':' "
                "a21=':' a22=':' a23=':' a24=':' a25=':'; unalias a1 a25; "
                # describe_command: keywords, aliases, functions, builtins, normal cmds
                "alias desc_al='echo hi'; desc_fn() { :; }; "
                "type if desc_al desc_fn : help echo path_sourced.sh >/dev/null; "
                "type -p if desc_al desc_fn : help echo path_sourced.sh >/dev/null; "
                "command -v if desc_al desc_fn : help echo path_sourced.sh >/dev/null; "
                "command -V if desc_al desc_fn : help echo path_sourced.sh >/dev/null; "
                # %builtin and %func in PATH + hash
                f'PATH="{hush_path_dir}:%builtin:{ash_func_dir}%func:{hush_path_dir}%unk:$PATH"; '
                "echo builtin_in_path >/dev/null; "
                "hash path_sourced.sh echo; hash; cd .; hash; "
                "ash_autoload_fn; "
                "(ash_bad_autoload) 2>/dev/null || true; "
                f'PATH="{hush_path_env}"; '
                # Process substitution <(cmd) and >(cmd) + xtrace
                "cat <(echo proc_sub_in) >/dev/null; "
                "cat >(cat >/dev/null) >/dev/null 2>&1 || true; "
                "ps_fn() { cat <(echo fn_proc_sub); : >(true); }; "
                "set -x; type ps_fn >/dev/null; ps_fn >/dev/null; set +x; "
                # Sourced script moving pf_fd and multiple args
                f'. "{ash_redir_src}" arg1 arg2 arg3; '
                # Redirections: fd==newfd, internally_opened_fd, noclobber, >|, <&
                "echo same_fd 1>&1 >/dev/null; "
                "(echo bad_internal >&10) 10>/dev/null 2>/dev/null || true; "
                "cat <&0 </dev/null; "
                "fd_zero=0; cat <&$fd_zero </dev/null; "
                "(bad_fd='not_num'; cat <&$bad_fd </dev/null) 2>/dev/null || true; "
                "(empty_fd=''; echo hi <&$empty_fd) 2>/dev/null || true; "
                "set -C; rm -f noclob.txt; echo ok >noclob.txt; "
                "(echo fail >noclob.txt) 2>/dev/null || true; "
                "echo clob_ok >|noclob.txt; "
                "echo ok >/dev/null; set +C; "
                "case x in x) : ;; esac; "
                # Special builtin redirect error and function special builtin error
                "(: </nonexistent_spcl_redir) 2>/dev/null || true; "
                "(bad_spcl_fn() { shift 99; }; bad_spcl_fn) 2>/dev/null || true; "
                # Function with ERR trap set before and modified inside
                "trap 'echo err_trap' ERR; "
                "err_fn1() { :; }; err_fn1; "
                "err_fn2() { trap 'echo err_trap2' ERR; }; err_fn2; "
                "trap - ERR; "
                # BASH_XTRACEFD and keyword xtrace quoting
                "BASH_XTRACEFD=2; set -x; "
                "echo if then `echo x` [a] a\\b {a}; set -; unset BASH_XTRACEFD; "
                # Multiple heredocs, partial EOF mark, quoted heredoc, backquote in EOF mark
                "cat <<EOF1 <<EOF2 <<EOF3 >/dev/null\nhd1\nEOF1\nhd2\nEOF2\nhd3\nEOF3\n"
                "cat <<-'EOF_TAB' >/dev/null\n\tlit_tab_line\n\tEOF_TAB_NOT\n\tEOF_TAB\n"
                "cat <<EOF_PART >/dev/null\nEOF_PART_EXTRA\nEOF_PART\n"
                "cat <<EOF`a` >/dev/null\nheredoc_bq_eof\nEOF`a`\n"
                'cat <<EOF_Q >/dev/null\n"quoted_in_hd"\nEOF_Q\n'
                # Nested aliases to exercise pushstring/popstring/freestrings allocation
                "alias nest1='nest2 ' nest2='echo nested_alias_ok'\n"
                "nest1\n"
                # Backquote with escaped chars, newlines, multiple backquotes, empty backquote
                "bq_nl=`echo a\\b\necho c`; echo `echo one`_`echo two` `` `:` >/dev/null; "
                # Arithmetic in double quotes, var inside arith, and multiple IFS regions
                'a_v=3; a_dq="$((1 + ${a_v:-2}))"; '
                "w1='a b'; w2='c d'; echo $w1$w2$((1+1)) $w1$w2${w1#a} >/dev/null; "
                # IFS consecutive non-whitespace delimiters
                "IFS=': '; s_ifs='a::b: :c'; set -- $s_ifs; IFS=$' \\t\\n'; "
                # Tilde with quotes/colon, pattern subst and substr edge cases
                "echo ~\\root ~'root' >/dev/null; t_col=~:~; echo ${x:-~:~} >/dev/null; "
                "v_sub='a*b*c*d'; echo ${v_sub:1:2} \"${v_sub:1:2}\" >/dev/null; "
                'v_esc=\'a*b*c\\d!^\'; echo "${v_esc#\\*}" "${v_esc%\\*}" "${v_esc//a/\\\\}" '
                "${v_esc//\\!/X} ${v_esc//\\^/Y} ${v_esc/a/} >/dev/null; "
                "echo ${v_sub/'a'/X} ${#unset_len_v} ${1:+${#1}} "
                "${1:+`echo x`} ${1:+$((1+1))} ${1:+~} >/dev/null; "
                "set -u; (echo ${unset_u_var}) 2>/dev/null || true; set +u; "
                # set -f glob bypass, escaped glob patterns, multi-component glob
                "set -f; echo glob_d/* >/dev/null; set +f; "
                "mkdir -p glob_d/sub\\dir/deep; "
                "touch glob_d/.hidden glob_d/sub\\dir/deep/leaf\\file; "
                "echo glob_d/\\[a-c\\].txt glob_d/[a\\-c].txt glob_d/\\a.txt "
                "glob_d/[a/b glob_d/nonexist_dir_xyz/*.txt "
                "glob_d/\\.* glob_d/sub\\\\dir/*/leaf\\\\file >/dev/null; "
                # For loop without 'in' and without semicolon (newline before do)
                "set -- for_arg1; for f_no_in\ndo echo $f_no_in >/dev/null; done; "
                # Background pipeline, while/until return in condition, if test return
                "echo bg_pipe | cat & wait; "
                "fn_if_ret() { if return 4; then :; fi; }; fn_if_ret || true; "
                "fn_wh_ret() { while return 5; do :; done; }; fn_wh_ret || true; "
                "fn_un_ret() { until return 6; do :; done; }; fn_un_ret || true; "
                # Grow job table (>4 background jobs) and job lookup edge cases
                "usleep 200000 & usleep 200000 & usleep 200000 & usleep 200000 & usleep 200000 & "
                "(wait %usleep) 2>/dev/null || true; "
                "(wait %?usleep) 2>/dev/null || true; "
                "(wait badjob) 2>/dev/null || true; "
                "(wait %) 2>/dev/null || true; "
                "(wait %99) 2>/dev/null || true; "
                "wait; "
                "(wait %1) 2>/dev/null || true; "
                "usleep 100000 & wait %usleep; "
                # Wait interrupted by trapped signal, and trap while in function return
                "trap 'echo wait_sig_usr1' USR1; "
                "(usleep 50000; kill -USR1 $$) & wait || true; "
                "fn_trap_ret() { kill -USR1 $$; return 0; }; fn_trap_ret; "
                "trap - USR1; "
                # Read timeout > 0
                "echo timed_in | read -t 1 rd_t_var",
            ],
            cwd=work,
        ),
        "nested_alias_ok",
    )
    run_bb(
        busybox,
        ["ash", "-i", "-I"],
        stdin_data=b"PS1='$((1+))'\n\n",
        cwd=work,
    )


def run_networking_suite(busybox: Path, work: Path) -> None:
    """Verify networking applets using deterministic loopback sockets.

    Args:
        busybox: Path to the compiled ``busybox`` executable.
        work: Isolated temporary directory for test fixtures.
    """
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_sock.bind(("127.0.0.1", 0))
    http_port = srv_sock.getsockname()[1]
    srv_sock.listen(1)

    def serve_http_once() -> None:
        """Serve a single HTTP 200 chunked response over loopback."""
        try:
            conn, _ = srv_sock.accept()
            with conn:
                conn.recv(1024)
                body = f"wget_loopback_ok{NL}".encode()
                chunk = (
                    f"{len(body):x}{CRLF}".encode("ascii")
                    + body
                    + f"{CRLF}0{CRLF}{CRLF}".encode("ascii")
                )
                resp = (f"HTTP/1.1 200 OK{CRLF}Transfer-Encoding: Chunked{CRLF}{CRLF}").encode(
                    "ascii"
                ) + chunk
                conn.sendall(resp)
        finally:
            srv_sock.close()

    http_thread = threading.Thread(target=serve_http_once, daemon=True)
    http_thread.start()
    wget_out = work / "wget_result.txt"
    assert_ok(
        run_bb(
            busybox,
            [
                "wget",
                "-q",
                "-O",
                str(wget_out),
                f"http://127.0.0.1:{http_port}/index.txt",
            ],
        )
    )
    http_thread.join(timeout=5.0)
    assert wget_out.read_text(encoding="utf-8") == f"wget_loopback_ok{NL}"

    nc_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    nc_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    nc_srv.bind(("127.0.0.1", 0))
    nc_port = nc_srv.getsockname()[1]
    nc_srv.listen(1)
    received: list[bytes] = []

    def serve_nc_once() -> None:
        """Receive one payload over TCP and reply with nc_pong."""
        try:
            conn, _ = nc_srv.accept()
            with conn:
                data = conn.recv(1024)
                received.append(data)
                conn.sendall(f"nc_pong{NL}".encode())
        finally:
            nc_srv.close()

    nc_thread = threading.Thread(target=serve_nc_once, daemon=True)
    nc_thread.start()
    assert_ok(
        run_bb(
            busybox,
            ["nc", "127.0.0.1", str(nc_port)],
            stdin_data=f"nc_ping{NL}".encode(),
        ),
        "nc_pong",
    )
    nc_thread.join(timeout=5.0)
    assert b"nc_ping" in b"".join(received)

    # Verify httpd serving a static directory
    www_dir = work / "www"
    www_dir.mkdir()
    (www_dir / "hello.txt").write_text(f"httpd_served_ok{NL}", encoding="utf-8")
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    httpd_port = probe.getsockname()[1]
    probe.close()
    httpd_proc = subprocess.Popen(
        _bb_cmd(
            busybox,
            [
                "httpd",
                "-f",
                "-v",
                "-p",
                f"127.0.0.1:{httpd_port}",
                "-h",
                str(www_dir),
            ],
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_tcp_ready(httpd_port)
        httpd_out = work / "httpd_fetched.txt"
        assert_ok(
            run_bb(
                busybox,
                [
                    "wget",
                    "-O",
                    str(httpd_out),
                    f"http://127.0.0.1:{httpd_port}/hello.txt",
                ],
            )
        )
        assert httpd_out.read_text(encoding="utf-8") == f"httpd_served_ok{NL}"
        for ping_args in (
            ["ping", "127.0.0.1"],
            ["ping", "-4", "127.0.0.1"],
            ["ping", "-6", "127.0.0.1"],
            ["ping6", "127.0.0.1"],
        ):
            assert_ok(run_bb(busybox, ping_args), "is alive!")
        for bad_ping in (["ping"], ["ping", "-z", "127.0.0.1"]):
            assert run_bb(busybox, bad_ping).returncode != 0
        run_bb(
            busybox,
            [
                "wget",
                "-q",
                f"http://a+b%20%41%4a%g1%4g:pass@127.0.0.1:{httpd_port}/hello.txt",
            ],
            cwd=work,
        )
    finally:
        httpd_proc.wait(timeout=3.0)
    for http_req in (
        f"GET /a+b%20%41%4a HTTP/1.0{CRLF}{CRLF}".encode("ascii"),
        f"GET /%g1 HTTP/1.0{CRLF}{CRLF}".encode("ascii"),
        f"GET /%4g HTTP/1.0{CRLF}{CRLF}".encode("ascii"),
        f"GET /%2f HTTP/1.0{CRLF}{CRLF}".encode("ascii"),
        f"GET /%00 HTTP/1.0{CRLF}{CRLF}".encode("ascii"),
    ):
        assert_ok(run_bb(busybox, ["httpd", "-i"], cwd=www_dir, stdin_data=http_req))
    assert_ok(run_bb(busybox, ["httpd", "-d", "hello%20world"]), "hello world")
    assert run_bb(busybox, ["httpd", "-i", "-c", str(work / "no_httpd.conf")]).returncode != 0
    www_sub = www_dir / "sub"
    www_sub.mkdir(exist_ok=True)
    (www_dir / "idx2.html").write_text("index_page_ok\n", encoding="utf-8")
    (www_sub / "item.xyz").write_text("xyz_ok\n", encoding="utf-8")
    (www_sub / "item.av").write_text("av_ok\n", encoding="utf-8")
    (www_sub / "item.unknown").write_text("unk_ok\n", encoding="utf-8")
    httpd_conf_text = (
        f"# comment line{NL}"
        f"   {NL}"
        f"I:idx1.html{NL}"
        f"I:idx2.html{NL}"
        f"H:.{NL}"
        f"  .xyz : text/xyz \t # inline{NL}"
        f".abc:text/abc{NL}"
        f"bad_no_colon{NL}"
        f"bad_empty:{NL}"
        f"Z:unknown{NL}"
    )
    (www_dir / "httpd.conf").write_text(httpd_conf_text, encoding="utf-8")
    (www_sub / "httpd.conf").write_text(f".xyz:text/subxyz{NL}.def:text/def{NL}", encoding="utf-8")
    for inetd_req in (
        b"",
        b"GET /\x01 HTTP/1.0\r\n\r\n",
        b"GET /\x7f HTTP/1.0\r\n\r\n",
        b"GET /\t HTTP/1.0\r\n\r\n",
        b"GET /" + (b"a" * 8200) + b" HTTP/1.0\r\n\r\n",
        b"BADREQ\r\n\r\n",
        b"GET /no_http_ver\r\n\r\n",
        b"GET / BAD/1.0\r\n\r\n",
        b"GET noslash HTTP/1.0\r\n\r\n",
        b"POST /hello.txt HTTP/1.0\r\n\r\n",
        b"HEAD /hello.txt HTTP/1.0\r\n\r\n",
        b"HEAD /missing.txt HTTP/1.0\r\n\r\n",
        b"GET /hello.txt?foo=bar HTTP/1.0\r\nX-Hdr: 1\r\n\r\n",
        b"GET //./sub/../hello.txt HTTP/1.0\r\n\r\n",
        b"GET /.hidden HTTP/1.0\r\n\r\n",
        b"GET /../hello.txt HTTP/1.0\r\n\r\n",
        b"GET / HTTP/1.0\r\n\r\n",
        b"GET /sub?q=1 HTTP/1.0\r\n\r\n",
        b"GET /sub HTTP/1.0\r\n\r\n",
        b"GET /httpd.conf HTTP/1.0\r\n\r\n",
        b"GET /sub/item.xyz HTTP/1.0\r\n\r\n",
        b"GET /sub/item.av HTTP/1.0\r\n\r\n",
        b"GET /sub/item.unknown HTTP/1.0\r\n\r\n",
    ):
        run_bb(
            busybox,
            ["httpd", "-i", "-v", "-v", "-v", "-c", str(www_dir / "httpd.conf")],
            cwd=www_dir,
            stdin_data=inetd_req,
        )
    assert run_bb(busybox, ["httpd", "-f", "-h", str(work / "no_such_www_dir")]).returncode != 0
    assert (
        run_bb(
            busybox,
            ["httpd", "-f", "-p", str(httpd_port), "-h", str(work / "no_such_www_dir")],
        ).returncode
        != 0
    )
    httpd_once_dir = work / "httpd_once"
    httpd_once_dir.mkdir(parents=True, exist_ok=True)
    ho_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ho_probe.bind(("127.0.0.1", 0))
    ho_port = ho_probe.getsockname()[1]
    ho_probe.close()
    assert_ok(run_bb(busybox, ["httpd", "-p", str(ho_port), "-h", str(httpd_once_dir)]))
    assert_ok(run_bb(busybox, ["httpd", "-f", "-p", str(ho_port), "-h", str(httpd_once_dir)]))
    assert_ok(
        run_bb(busybox, ["httpd", "-f", "-p", f"127.0.0.1:{ho_port}", "-h", str(httpd_once_dir)])
    )
    assert run_bb(busybox, ["telnetd", "-F", "-p", "65536"]).returncode != 0
    assert run_bb(busybox, ["ping", "localhost:99999"]).returncode != 0
    assert run_bb(busybox, ["ping", "nonexistent_host_xyz.invalid"]).returncode != 0
    assert_ok(run_bb(busybox, ["ping", "localhost"]), "is alive!")

    issue_f = work / "issue.txt"
    issue_f.write_bytes(b"\\S \\s \\n \\h \\r \\v \\m \\d \\t \\l \\z %% hi\xffthere\n")
    td_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    td_probe.bind(("127.0.0.1", 0))
    td_port = td_probe.getsockname()[1]
    td_probe.close()
    td_script = work / "td_child.sh"
    td_script.write_bytes(b"#!/bin/sh\ncat > /dev/null\n")
    td_script.chmod(0o755)
    td_proc = subprocess.Popen(
        _bb_cmd(
            busybox,
            [
                "telnetd",
                "-F",
                "-f",
                str(issue_f),
                "-b",
                "127.0.0.1",
                "-p",
                str(td_port),
                "-l",
                str(td_script),
            ],
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.08)
        assert (
            run_bb(
                busybox,
                [
                    "telnetd",
                    "-F",
                    "-b",
                    "127.0.0.1",
                    "-p",
                    str(td_port),
                    "-l",
                    "/nonexistent_login_xyz",
                ],
            ).returncode
            != 0
        )
        with socket.create_connection(("127.0.0.1", td_port), timeout=2.0) as td_cli:
            time.sleep(0.05)
            td_cli.recv(1024)
            td_cli.sendall(b"plain_no_cr_or_iac")
            time.sleep(0.05)
            td_cli.sendall(
                b"a\r\nb\r\x00c\rXd\xff\xffe\xff\xf6\xff\xf1\xff\xef\x01"
                b"\xff\xfa\x1f\x00\x50\x00\x18\xff\xf0"
                b"\xff\xfa\x1f\x00\xff\xff\x00\x00\xff\xf0"
                b"\xff\xfa\x1f\x00\x00\x00\x18\xff\xf0"
                b"\xff\xfa\x01\xff\xfb\x01"
            )
            time.sleep(0.1)
            with contextlib.suppress(OSError):
                td_cli.recv(1024)
    finally:
        td_proc.wait(timeout=3.0)

    td_probe2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    td_probe2.bind(("127.0.0.1", 0))
    td_port2 = td_probe2.getsockname()[1]
    td_probe2.close()
    td_bg = subprocess.run(
        _bb_cmd(
            busybox,
            [
                "telnetd",
                "-K",
                "-b",
                f"127.0.0.1:{td_port2}",
                "-l",
                "/nonexistent_login_xyz",
            ],
        ),
        capture_output=True,
        check=False,
    )
    assert td_bg.returncode == 0
    td_probe3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    td_probe3.bind(("127.0.0.1", 0))
    td_port3 = td_probe3.getsockname()[1]
    td_probe3.close()
    td_proc3 = subprocess.Popen(
        _bb_cmd(
            busybox,
            [
                "telnetd",
                "-F",
                "-K",
                "-f",
                str(issue_f),
                "-l",
                "/usr/bin/true",
                "-b",
                f"127.0.0.1:{td_port3}",
            ],
        ),
        stdin=subprocess.DEVNULL,
        preexec_fn=lambda: os.close(0),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.08)
        with socket.create_connection(("127.0.0.1", td_port3), timeout=2.0) as td_cli3:
            td_cli3.sendall(b"after_true_exits\r\n")
            while td_cli3.recv(1024):
                pass
    finally:
        td_proc3.wait(timeout=3.0)

    # Verify dnsd answering UDP queries and exiting cleanly on id 0x9999
    dns_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns_probe.bind(("127.0.0.1", 0))
    dns_port = dns_probe.getsockname()[1]
    dns_probe.close()
    dns_conf = work / "dnsd.conf"
    dns_conf.write_text(
        f"# comment{NL}{NL}"
        f"test.local 127.0.0.42 # inline{NL}"
        f"*.wild.local 127.0.0.43{NL}"
        f".dot.local 127.0.0.44{NL}"
        f"* 127.0.0.99{NL}",
        encoding="utf-8",
    )
    dns_proc = subprocess.Popen(
        _bb_cmd(
            busybox,
            [
                "dnsd",
                "-v",
                "-t",
                "60",
                "-c",
                str(dns_conf),
                "-p",
                str(dns_port),
                "-i",
                "127.0.0.1",
            ],
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        dns_query = (
            b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04test\x05local\x00\x00\x01\x00\x01"
        )
        dns_resp = _query_udp_with_retry(dns_port, dns_query)
        assert dns_resp.endswith(b"\x7f\x00\x00\x2a")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as usock:
            usock.settimeout(0.5)
            for pkt in (
                b"short",
                b"x" * 513,
                b"\x12\x35\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                b"\x12\x36\x80\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x01",
                b"\x12\x37\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00",
                b"\x12\x38\x08\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04test\x05local\x00\x00\x01\x00\x01",
                b"\x12\x39\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04test\x05local\x00\x00\x01\x00\x02",
                b"\x12\x3a\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04test\x05local\x00\x00\x1c\x00\x01",
                b"\x12\x3b\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03sub\x04wild\x05local\x00\x00\x01\x00\x01",
                b"\x12\x3c\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x0242\x010\x010\x03127\x07in-addr\x04arpa\x00\x00\x0c\x00\x01",
                b"\x99\x99\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x05other\x05local\x00\x00\x01\x00\x01",
            ):
                usock.sendto(pkt, ("127.0.0.1", dns_port))
                if pkt[:2] in (
                    b"\x12\x38",
                    b"\x12\x39",
                    b"\x12\x3a",
                    b"\x12\x3b",
                    b"\x12\x3c",
                    b"\x99\x99",
                ):
                    usock.recvfrom(1024)
    finally:
        dns_proc.wait(timeout=3.0)

    for td_stdin in (
        b"\xff\xfb\x01hello\r\nworld",
        b"\xff",
        b"\xff\xfb",
        b"\xff\xfa\x1f\x00",
        b"\xff\xfa\x1f\x00\xff\xff\x00\x00\xff",
    ):
        assert_ok(
            run_bb(
                busybox,
                ["telnetd", "-i", "-f", str(issue_f), "-l", "/usr/bin/true"],
                stdin_data=td_stdin,
            )
        )
    for bad_nc_port in ("", "nonexistent_service_xyz_999"):
        assert run_bb(busybox, ["nc", "127.0.0.1", bad_nc_port]).returncode != 0
    for bad_nc_args in (["nc", "-z"], ["nc", "127.0.0.1"]):
        assert run_bb(busybox, bad_nc_args).returncode != 0
    run_bb(busybox, ["nc", "-w", "1", "127.0.0.1", "daytime"])

    # Verify telnet client receiving banner and negotiating options with loopback server
    for bad_tel in (["telnet"], ["telnet", "127.0.0.1", "23", "extra"]):
        assert run_bb(busybox, bad_tel).returncode != 0
    run_bb(busybox, ["telnet", "127.0.0.1"], stdin_data=b"\x1de")

    def run_telnet_interactive(
        server_chunks: list[bytes],
        stdin_chunks: list[bytes],
        expect_ok: bool = True,
    ) -> None:
        """Run an interactive telnet session with phased server and stdin writes."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)

        proc = subprocess.Popen(
            _bb_cmd(busybox, ["telnet", "127.0.0.1", str(port)]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(0.5)
                for ch in server_chunks:
                    with contextlib.suppress(OSError):
                        conn.sendall(ch)
                    time.sleep(0.04)
                    with contextlib.suppress(OSError):
                        conn.recv(1024)
                assert proc.stdin is not None
                for sch in stdin_chunks:
                    with contextlib.suppress(OSError):
                        proc.stdin.write(sch)
                        proc.stdin.flush()
                    time.sleep(0.03)
                    with contextlib.suppress(OSError):
                        conn.recv(1024)
                with contextlib.suppress(OSError):
                    proc.stdin.close()
                time.sleep(0.04)
        finally:
            srv.close()
        proc.wait(timeout=3.0)
        if expect_ok:
            assert proc.returncode == 0
        else:
            assert proc.returncode != 0

    run_telnet_interactive(
        [
            b"just_ascii",
            b"\xff\xfb\x03",
            b"\xff\xfc\x03",
            b"\xff",
            b"\xfb\x01\xff\xfb\x03\xff\xfb\x99\xff\xfd\x01\xff\xfe\x01"
            b"\xff\xff\xff\xf0\xff\xfa\x01\xff\x02\xff\xf0\r\x00\rX\r\xff\xff"
            + f"telnet_banner_ok{CRLF}".encode("ascii"),
            b"\xff\xfc\x01",
            b"\xff\xfb\x01\xff\xfa" + (b"A" * 260),
        ],
        [
            b"plain_only",
            b"line1",
            b"line2",
            b"line3",
            b"a\xff\r\nb",
            b"\x1d",
            b"l",
            b"\x1d",
            b"l",
            b"\x1d",
            b"c",
            b"\x1d",
            b"c",
            b"\x1d",
            b"?",
        ],
        expect_ok=True,
    )
    run_telnet_interactive(
        [b"plain_banner\r\n"],
        [b"\x1d", b"c", b"\x1d", b"?", b"\x1d", b"e"],
        expect_ok=False,
    )
    run_telnet_interactive(
        [b"plain_banner\r\n"],
        [b"\x1d"],
        expect_ok=False,
    )

    # Verify wget against scripted HTTP scenarios (chunked, redirects, proxy, ranges, errors)
    wget_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    wget_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    wget_srv.bind(("127.0.0.1", 0))
    wget_port = wget_srv.getsockname()[1]
    wget_srv.listen(32)
    wget_Big = b"B" * 5000
    wget_scenarios: list[bytes | Callable[[socket.socket], None]] = [
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX.Custom_Hdr: 1\r\nX-Empty: \r\n\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
        ),
        b"HTTP/1.1 200\tOK\r\n\r\nplain_eof_body",
        f"HTTP/1.1 200 OK\r\nContent-Length: {len(wget_Big)}\r\n\r\n".encode("ascii") + wget_Big,
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
        (
            b"HTTP/1.1 100 Continue\r\nX-Tmp: 1\r\n\r\n"
            b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nfull"
        ),
        b"HTTP/1.1 206 Partial Content\r\nContent-Length: 2\r\n\r\nok",
        b"HTTP/1.1 302 Found\r\nLocation: /r1  \t\r\n\r\n",
        f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{wget_port}/r2\r\n\r\n".encode("ascii"),
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
        f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{wget_port}/r3\r\n\r\n".encode("ascii"),
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort",
        b"HTTP/1.1 206 Partial Content\r\nContent-Length: 95\r\n\r\n" + (b"R" * 95),
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort",
        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort",
        b"",
        b"HTTP/1.1 500 NoNewline",
        b"HTTP/1.1 206 Unexpected\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nBadHeaderNoColon\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: -5\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 999999999999999999999999999\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: deflate\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n-5\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nffffffffffffffffffffffff\r\n",
    ]
    for _ in range(16):
        wget_scenarios.append(b"HTTP/1.1 302 Found\r\nLocation: /loop\r\n\r\n")

    def stall_conn(conn: socket.socket) -> None:
        """Stall connection for 1.2s to trigger wget -T 1 alarm timeout."""
        time.sleep(1.2)

    wget_scenarios.append(stall_conn)

    def serve_wget_scenarios() -> None:
        """Serve each scripted HTTP response in order."""
        try:
            for item in wget_scenarios:
                conn, _ = wget_srv.accept()
                with conn:
                    conn.settimeout(0.5)
                    if callable(item):
                        item(conn)
                    else:
                        with contextlib.suppress(OSError):
                            conn.recv(1024)
                        if item:
                            conn.sendall(item)
        finally:
            wget_srv.close()

    wget_thread = threading.Thread(target=serve_wget_scenarios, daemon=True)
    wget_thread.start()
    w_out = work / "-wget_out"
    w_pfx = work / "wget_pfx"
    w_pfx.mkdir()
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-S", "-T", "0", "-O", "-wget_out", f"http://127.0.0.1:{wget_port}/c"],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-o", "-", "-O", "-", f"http://127.0.0.1:{wget_port}/eof"],
            cwd=work,
        ),
        "plain_eof_body",
    )
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-o", "wget.log", "-P", str(w_pfx), f"http://127.0.0.1:{wget_port}/"],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "env",
                "http_proxy=",
                str(busybox.resolve()),
                "wget",
                "-o",
                "-wget_log",
                "-O",
                "-wget_out",
                f"127.0.0.1:{wget_port}?query",
                f"127.0.0.1:{wget_port}",
            ],
            cwd=work,
        )
    )
    w_cont = work / "cont.bin"
    w_cont.write_bytes(b"ab")
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-q", "-c", "-O", str(w_cont), f"http://127.0.0.1:{wget_port}/cont1"],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-q", "-c", "-O", str(w_cont), f"http://127.0.0.1:{wget_port}/cont2"],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-q", "-Y", "off", "-O", str(w_out), f"http://127.0.0.1:{wget_port}/redir"],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "env",
                f"http_proxy=http://puser:ppass@127.0.0.1:{wget_port}",
                str(busybox.resolve()),
                "wget",
                "-q",
                "-O",
                str(w_out),
                f"http://127.0.0.1:{wget_port}/proxied",
            ],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            ["wget", "-q", "-t", "0", "-O", str(w_out), f"http://127.0.0.1:{wget_port}/retry_ok"],
            cwd=work,
        )
    )
    assert_ok(
        run_bb(
            busybox,
            [
                "wget",
                "-q",
                "-t",
                "1",
                "-O",
                str(w_out),
                f"http://127.0.0.1:{wget_port}/retry_exhaust",
            ],
            cwd=work,
        )
    )
    for bad_wget in (
        ["wget", "-q", f"ftp://127.0.0.1:{wget_port}/f"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/empty_eof"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/err500"],
        ["wget", "-q", "-c", f"http://127.0.0.1:{wget_port}/unexpected_206_newfile"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/bad_hdr"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/bad_clen_neg"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/bad_clen_ovf"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/bad_te"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/bad_chunk_neg"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/bad_chunk_ovf"],
        ["wget", "-q", f"http://127.0.0.1:{wget_port}/redir_loop"],
        ["wget", "-q", "-T", "1", f"http://127.0.0.1:{wget_port}/stall"],
    ):
        assert run_bb(busybox, bad_wget, cwd=work).returncode != 0
    wget_thread.join(timeout=5.0)

    # Verify ftpget and ftpput against a loopback PASV/EPSV FTP server
    def run_ftp_transfer(is_get: bool, payload: bytes) -> bytes:
        """Execute an FTP RETR or STOR session against a loopback mock server.

        Args:
            is_get: True to run ``ftpget`` (RETR), False to run ``ftpput`` (STOR).
            payload: File content bytes to serve or upload.

        Returns:
            Transferred bytes verified by the client or server.
        """
        ctrl_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ctrl_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ctrl_srv.bind(("127.0.0.1", 0))
        ctrl_port = ctrl_srv.getsockname()[1]
        ctrl_srv.listen(1)

        data_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        data_srv.bind(("127.0.0.1", 0))
        data_port = data_srv.getsockname()[1]
        data_srv.listen(1)

        stored_chunks: list[bytes] = []

        def serve_ftp() -> None:
            """Handle a single FTP control and passive data connection."""
            try:
                conn, _ = ctrl_srv.accept()
                with conn:
                    stream = conn.makefile("rwb", buffering=0)
                    stream.write(f"220 mock ftp{CRLF}".encode("ascii"))
                    while True:
                        raw_line = stream.readline()
                        cmd = raw_line.decode("ascii", errors="ignore").strip().upper()
                        if cmd.startswith("USER"):
                            stream.write(f"230 logged in{CRLF}".encode("ascii"))
                        elif cmd.startswith("SIZE"):
                            stream.write(f"213 {len(payload)}{CRLF}".encode("ascii"))
                        elif cmd.startswith("PASV"):
                            p1, p2 = data_port >> 8, data_port & 0xFF
                            stream.write(
                                f"227 Entering Passive Mode (127,0,0,1,{p1},{p2}){CRLF}".encode(
                                    "ascii"
                                )
                            )
                        elif cmd.startswith("RETR"):
                            stream.write(f"150 opening data{CRLF}".encode("ascii"))
                            dconn, _ = data_srv.accept()
                            with dconn:
                                dconn.sendall(payload)
                            stream.write(f"226 transfer complete{CRLF}".encode("ascii"))
                        elif cmd.startswith("STOR"):
                            stream.write(f"150 opening data{CRLF}".encode("ascii"))
                            dconn, _ = data_srv.accept()
                            with dconn:
                                while True:
                                    chunk = dconn.recv(4096)
                                    if not chunk:
                                        break
                                    stored_chunks.append(chunk)
                            stream.write(f"226 transfer complete{CRLF}".encode("ascii"))
                        elif cmd.startswith("QUIT"):
                            stream.write(f"221 bye{CRLF}".encode("ascii"))
                            break
                        else:
                            stream.write(f"200 ok{CRLF}".encode("ascii"))
            finally:
                ctrl_srv.close()
                data_srv.close()

        ftp_thread = threading.Thread(target=serve_ftp, daemon=True)
        ftp_thread.start()
        if is_get:
            dst_file = work / "ftp_downloaded.txt"
            assert_ok(
                run_bb(
                    busybox,
                    [
                        "ftpget",
                        "-P",
                        str(ctrl_port),
                        "127.0.0.1",
                        str(dst_file),
                        "remote.txt",
                    ],
                )
            )
            ftp_thread.join(timeout=5.0)
            return dst_file.read_bytes()
        src_file = work / "ftp_upload.txt"
        src_file.write_bytes(payload)
        assert_ok(
            run_bb(
                busybox,
                [
                    "ftpput",
                    "-P",
                    str(ctrl_port),
                    "127.0.0.1",
                    "remote.txt",
                    str(src_file),
                ],
            )
        )
        ftp_thread.join(timeout=5.0)
        return b"".join(stored_chunks)

    assert run_ftp_transfer(True, f"ftpget_ok{NL}".encode()) == (f"ftpget_ok{NL}".encode())
    assert run_ftp_transfer(False, f"ftpput_ok{NL}".encode()) == (f"ftpput_ok{NL}".encode())

    for pasv_reply in (
        f"227 Entering Passive Mode 127,0,0,1,0,1{CRLF}".encode("ascii"),
        f"227 Entering Passive Mode 127,0,0,1,0,1{NL}".encode("ascii"),
        b"227 nocommas",
        f"227 one,80){CRLF}".encode("ascii"),
    ):
        bad_ftp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bad_ftp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bad_ftp.bind(("127.0.0.1", 0))
        bad_ftp_port = bad_ftp.getsockname()[1]
        bad_ftp.listen(1)

        def serve_bad_pasv(srv: socket.socket = bad_ftp, reply: bytes = pasv_reply) -> None:
            """Serve a synthetic PASV response to exercise parse_pasv_epsv branches."""
            try:
                conn, _ = srv.accept()
                with conn:
                    stream = conn.makefile("rwb", buffering=0)
                    stream.write(f"220 mock{CRLF}".encode("ascii"))
                    while True:
                        cmd = stream.readline().decode("ascii", errors="ignore").strip().upper()
                        if cmd.startswith("USER"):
                            stream.write(f"230 ok{CRLF}".encode("ascii"))
                        elif cmd.startswith("PASV"):
                            stream.write(reply)
                            break
                        else:
                            stream.write(f"200 ok{CRLF}".encode("ascii"))
            finally:
                srv.close()

        bad_thread = threading.Thread(target=serve_bad_pasv, daemon=True)
        bad_thread.start()
        run_bb(
            busybox,
            [
                "ftpget",
                "-P",
                str(bad_ftp_port),
                "127.0.0.1",
                str(work / "bad_ftp_out.txt"),
                "remote.txt",
            ],
        )
        bad_thread.join(timeout=5.0)

    def run_custom_ftp(
        replies: dict[str, list[str]],
        cli_args: list[str],
        stdin_data: bytes = b"",
        open_data: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run ftpget/ftpput against a custom scripted FTP server.

        Args:
            replies: Mapping of FTP verb or stage name to response lines.
            cli_args: Applet name and arguments to pass to BusyBox.
            stdin_data: Bytes piped to standard input of the applet.
            open_data: Whether to accept and service the passive data connection.

        Returns:
            CompletedProcess result from the BusyBox invocation.
        """
        c_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        c_srv.bind(("127.0.0.1", 0))
        c_port = c_srv.getsockname()[1]
        c_srv.listen(1)

        d_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        d_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        d_srv.bind(("127.0.0.1", 0))
        d_port = d_srv.getsockname()[1]
        d_srv.listen(1)

        def serve_scripted() -> None:
            """Serve scripted FTP control responses."""
            try:
                conn, _ = c_srv.accept()
                with conn:
                    stream = conn.makefile("rwb", buffering=0)
                    for line in replies.get("BANNER", ["220 ready"]):
                        if line == "EOF":
                            return
                        stream.write(f"{line}{CRLF}".encode("latin1"))
                    while True:
                        raw = stream.readline()
                        if not raw:
                            break
                        verb = raw.decode("latin1", errors="ignore").strip().split(" ")[0].upper()
                        if verb == "PASV":
                            default_reply = ["PASV_OK"]
                        elif verb == "USER":
                            default_reply = ["230 ok"]
                        else:
                            default_reply = ["200 ok"]
                        script = replies.get(verb, default_reply)
                        for rline in script:
                            if rline == "PASV_OK":
                                p1, p2 = d_port >> 8, d_port & 0xFF
                                stream.write(
                                    f"227 Entering Passive Mode (127,0,0,1,{p1},{p2}){CRLF}".encode(
                                        "ascii"
                                    )
                                )
                            else:
                                stream.write(f"{rline}{CRLF}".encode("latin1"))
                        if verb in ("RETR", "STOR") and open_data:
                            dconn, _ = d_srv.accept()
                            with dconn:
                                if verb == "RETR":
                                    dconn.sendall(b"part")
                                else:
                                    dconn.recv(1024)
                            for fin_line in replies.get("DONE", ["226 done"]):
                                stream.write(f"{fin_line}{CRLF}".encode("latin1"))
                        if verb == "QUIT":
                            break
            finally:
                c_srv.close()
                d_srv.close()

        t = threading.Thread(target=serve_scripted, daemon=True)
        t.start()
        res = run_bb(
            busybox,
            [cli_args[0], "-P", str(c_port), *cli_args[1:]],
            cwd=work,
            stdin_data=stdin_data,
        )
        t.join(timeout=5.0)
        return res

    cont_nonempty = work / "cont_nonempty.txt"
    cont_nonempty.write_bytes(b"existing")
    for ftp_case in (
        ({"BANNER": ["EOF"]}, ["ftpget", "127.0.0.1", "remote.txt"], False),
        ({"BANNER": ["500 \x01\x7f\x80err"]}, ["ftpget", "127.0.0.1", "remote.txt"], False),
        (
            {"BANNER": ["220-multi", "abc", "220 ok"], "USER": ["530 denied"]},
            ["ftpget", "-v", "127.0.0.1", "remote.txt"],
            False,
        ),
        ({"USER": ["331 pass"], "PASS": ["530 bad"]}, ["ftpget", "127.0.0.1", "remote.txt"], False),
        (
            {
                "USER": ["331 pass"],
                "PASS": ["230 ok"],
                "PASV": ["PASV_OK"],
                "SIZE": ["550 no"],
                "RETR": ["150 ok"],
            },
            ["ftpget", "-c", "127.0.0.1", "-", "remote.txt"],
            True,
        ),
        ({"PASV": ["500 no pasv"]}, ["ftpget", "127.0.0.1", "remote.txt"], False),
        (
            {"PASV": ["PASV_OK"], "SIZE": ["213 10"], "RETR": ["550 fail"]},
            ["ftpget", "127.0.0.1", str(cont_nonempty), "remote.txt"],
            False,
        ),
        (
            {"PASV": ["PASV_OK"], "RETR": ["150 ok"], "DONE": ["500 transfer err"]},
            ["ftpget", "127.0.0.1", "-"],
            True,
        ),
        ({"PASV": ["PASV_OK"], "STOR": ["125 ok"]}, ["ftpput", "127.0.0.1", "-"], True),
        (
            {"PASV": ["PASV_OK"], "STOR": ["550 denied"]},
            ["ftpput", "127.0.0.1", "remote.txt", "-"],
            False,
        ),
    ):
        run_custom_ftp(ftp_case[0], ftp_case[1], stdin_data=b"upload", open_data=ftp_case[2])

    # Verify nslookup and ping execution paths
    ns_res = run_bb(busybox, ["nslookup", "localhost"])
    assert ns_res.returncode == 0 or b"Server:" in ns_res.stdout
    run_bb(busybox, ["nslookup", "203.0.113.1", "127.0.0.1"])
    run_bb(busybox, ["nslookup", "256.256.256.256", "127.0.0.1"])
    run_bb(busybox, ["nslookup", "localhost", "local:ns_sock"])
    for bad_ns in (["nslookup"], ["nslookup", "-x"], ["nslookup", "a", "b", "c"]):
        assert run_bb(busybox, bad_ns).returncode != 0
    ping_res = run_bb(busybox, ["ping", "127.0.0.1"])
    ping_combined = (ping_res.stdout + ping_res.stderr).decode("utf-8", errors="replace")
    assert "127.0.0.1" in ping_combined or "permission denied" in ping_combined.lower()


SUITES: dict[str, Callable[[Path, Path], None]] = {
    "coreutils": run_coreutils_suite,
    "editors": run_editors_and_find_suite,
    "archival": run_archival_suite,
    "procps": run_procps_and_shell_suite,
    "networking": run_networking_suite,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for ``run_applet_test.py``.

    Args:
        argv: Optional sequence of CLI arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed ``argparse.Namespace`` with ``busybox`` and ``suite``.
    """
    parser = argparse.ArgumentParser(description="Run functional BusyBox applet test suites.")
    parser.add_argument(
        "busybox",
        type=Path,
        help="Path to the compiled busybox binary",
    )
    parser.add_argument(
        "--suite",
        choices=[*SUITES.keys(), "all"],
        default="all",
        help="Test suite category to execute",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the requested BusyBox functional test suite(s).

    Args:
        argv: Optional sequence of CLI arguments.

    Returns:
        Exit status code (``0`` on success, ``1`` on failure).
    """
    args = parse_args(argv)
    if not args.busybox.is_file():
        print(f"Error: busybox binary not found: {args.busybox}", file=sys.stderr)
        return 1

    suites_to_run = list(SUITES.values()) if args.suite == "all" else [SUITES[args.suite]]
    try:
        for suite_fn in suites_to_run:
            with tempfile.TemporaryDirectory(prefix="bb_test_") as tmp_dir:
                suite_fn(args.busybox, Path(tmp_dir))
    except Exception as exc:
        print(f"Applet test suite {args.suite!r} failed: {exc}", file=sys.stderr)
        return 1

    print(f"BusyBox functional suite {args.suite!r} passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
