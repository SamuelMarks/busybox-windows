BusyBox Windows Native (MSVC) Builds
====================================

[![License](https://img.shields.io/badge/license-CC0%20OR%20Apache--2.0%20OR%20MIT-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Build and Release BusyBox Windows](https://github.com/SamuelMarks/busybox-windows/actions/workflows/release.yml/badge.svg)](https://github.com/SamuelMarks/busybox-windows/actions/workflows/release.yml)

This repository provides **native Windows builds** of [BusyBox](https://busybox.net/)—"The Swiss Army Knife of Embedded Linux"—compiled with Microsoft Visual C++ (MSVC) and CMake.

BusyBox combines tiny versions of many common UNIX utilities into a single multi-call binary. Historically, running BusyBox on Windows required virtualization (WSL2), heavy runtime emulation environments (Cygwin/MSYS2 DLLs), or complex unmaintained forks.

This project's mission is to provide true native Windows binaries (`busybox.exe`) that compile cleanly under MSVC without requiring runtime DLL emulators.

---

## Architecture: `auto-win-msvc` + Declarative Patches

All POSIX system calls, process spawning, file descriptors, terminal handling, `/proc` enumeration, and signals are backed directly by the modular **[auto-win-msvc](https://github.com/SamuelMarks/auto-win-msvc)** compatibility layer.

By separating POSIX/Linux platform polyfills from the BusyBox build harness:
- **Zero `.c`/`.h` Files in `busybox-windows`:** No `.h` or `.c` files are stored in `busybox-windows`. All general POSIX/Linux/BSD polyfills live in `auto-win-msvc`, and strictly BusyBox-internal MSVC adaptations live as declarative, DEP-3 documented unified diffs in `patches/` applied idempotently via `scripts/apply_patches.py`.
- **Single-Source-of-Truth Applet Manifest:** `scripts/applets_manifest.py` declaratively defines all 135 enabled applets, their dispatch macros, Kconfig symbols, and required source files, driving both `scripts/generate_autoconf.py` and `scripts/generate_applet_tables.py` at build time.
- **High Performance:** Lightweight, native Windows execution with fast startup and low overhead.
- **100% Tested & Strongly Typed:** 100% Python line, branch, function, and docstring coverage (`pytest --cov --cov-branch --cov-fail-under=100`, `mypy --strict`, `interrogate --fail-under=100`) alongside functional CTest verification suites (`scripts/run_applet_test.py`) covering coreutils, archival, editors, findutils, procps, shells (`ash`, `hush`, `sh`), and loopback networking (`httpd`, `wget`, `nc`, `dnsd`, `nslookup`, `ftpget`, `ftpput`, `telnet`, `ping`).

---

## Enabled Applets (135 Total)

- **Coreutils & Text:** `[`, `[[`, `base32`, `base64`, `basename`, `cat`, `chmod`, `cksum`, `comm`, `cp`, `crc32`, `cut`, `date`, `df`, `dirname`, `dos2unix`, `du`, `echo`, `env`, `expand`, `factor`, `false`, `fold`, `head`, `id`, `ln`, `ls`, `md5sum`, `mkdir`, `mktemp`, `mv`, `nl`, `od`, `paste`, `printenv`, `printf`, `pwd`, `readlink`, `realpath`, `rm`, `rmdir`, `seq`, `sha1sum`, `sha256sum`, `sha384sum`, `sha3sum`, `sha512sum`, `shuf`, `sleep`, `sort`, `split`, `stat`, `sum`, `sync`, `tail`, `tee`, `test`, `touch`, `tr`, `true`, `truncate`, `tsort`, `uname`, `unexpand`, `uniq`, `unix2dos`, `unlink`, `usleep`, `uudecode`, `uuencode`, `wc`, `whoami`, `yes`
- **Archival & Compression:** `ar`, `bunzip2`, `bzcat`, `bzip2`, `cpio`, `gunzip`, `gzip`, `lzop`, `lzopcat`, `tar`, `unlzop`, `unxz`, `unzip`, `xz`, `xzcat`, `zcat`
- **Editors & Search:** `awk`, `cmp`, `diff`, `ed`, `egrep`, `fgrep`, `find`, `grep`, `patch`, `sed`, `vi`, `xargs`
- **Shells:** `ash`, `hush`, `sh`
- **Process & System Utilities:** `cal`, `clear`, `free`, `hexdump`, `hexedit`, `kill`, `killall`, `pgrep`, `pidof`, `pkill`, `ps`, `reset`, `strings`, `time`, `uptime`, `watch`, `which`, `xxd`, `bc`, `dc`
- **Networking:** `dnsd`, `ftpget`, `ftpput`, `httpd`, `nc`, `nslookup`, `ping`, `ping6`, `telnet`, `telnetd`, `wget`

---

## Releases

Pre-compiled packages are available on the [Releases](../../releases) tab.

### Included Packages:
- **NSIS Setup Installer (`BusyBox-*-win64.exe`):** Windows installer with option to add BusyBox to the system PATH.
- **Portable ZIP Archive (`BusyBox-*-win64.zip`):** Standalone archive containing `busybox.exe` and documentation.

---

## Building Locally

To build BusyBox for Windows from source using Visual Studio / MSVC:

1. Clone `busybox`, `auto-win-msvc`, and `busybox-windows` side-by-side:
   ```cmd
   git clone --depth 200 https://git.busybox.net/busybox
   git -C busybox checkout -B master e6302b2711d851bfb17eaf9316514c9d0a806cc7
   git clone https://github.com/SamuelMarks/auto-win-msvc.git
   git clone https://github.com/SamuelMarks/busybox-windows.git
   ```

2. Open an MSVC Developer Command Prompt and navigate to `busybox-windows`:
   ```cmd
   cd busybox-windows
   ```

3. Configure and compile using CMake:
   ```cmd
   cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
   cmake --build build --config Release
   ```

4. Run functional CTest and Python test suites:
   ```cmd
   ctest --test-dir build -C Release --output-on-failure
   pytest
   ```

5. Package installers (or run `release.bat` to automate steps 1–5):
   ```cmd
   cd build
   cpack -C Release -G ZIP
   cpack -C Release -G NSIS
   ```

---

## Quality & Coverage Gates

All Python build, codegen, patch-management, and test-orchestration modules enforce 100% docstring, type, line, branch, and function coverage via `pre-commit`:

```bash
pre-commit run --all-files
```

---

## License

This build system and integration packaging are tri-licensed under:
- [CC0 1.0 Universal](LICENSE-CC0)
- [Apache License 2.0](LICENSE-APACHE)
- [MIT License](LICENSE-MIT)

BusyBox itself is licensed under the GNU General Public License version 2 (GPL-2.0).
