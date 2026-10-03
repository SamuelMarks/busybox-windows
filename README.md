BusyBox Windows Native (MSVC) builds
====================================

[![License](https://img.shields.io/badge/license-CC0%20OR%20Apache--2.0%20OR%20MIT-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Build and Release BusyBox Windows](https://github.com/SamuelMarks/busybox-windows/actions/workflows/release.yml/badge.svg)](https://github.com/SamuelMarks/busybox-windows/actions/workflows/release.yml)

This repository provides **native Windows builds** of [BusyBox](https://busybox.net/)—"The Swiss Army Knife of Embedded Linux"—compiled with Microsoft Visual C++ (MSVC) and CMake.

BusyBox combines tiny versions of many common UNIX utilities into a single multi-call binary. Historically, running BusyBox on Windows required virtualization (WSL2), heavy runtime emulation environments (Cygwin/MSYS2 DLLs), or complex unmaintained forks.

This project's mission is to provide true native Windows binaries (`busybox.exe`) that compile cleanly under MSVC without requiring runtime DLL emulators.

---

## The Magic: `auto-win-msvc`

All POSIX system calls, process spawning, file descriptors, terminal handling, and signals are backed directly by the modular **[auto-win-msvc](https://github.com/SamuelMarks/auto-win-msvc)** compatibility layer.

By decoupling the Windows compatibility layer from the BusyBox implementation itself, we ensure:
- **Pristine Upstream Integrity:** Strict zero-modification policy for upstream code. No `.h` or `.c` files in `busybox` are modified. All compatibility is achieved through CMake configuration, forced includes, and modular polyfills.
- **High Performance:** Lightweight, native Windows execution with fast startup and low overhead.
- **Zero Namespace Pollution:** Clean separation without `<windows.h>` macro clashes (`near`, `far`, `min`, `max`, `ERROR`, etc.) or binary bloat.

---

## Key Features

- **Pure CMake Build Harness:** Standalone CMake configuration supporting MSVC 2005, 2019, 2022, 2026, MinGW, Clang, and GCC across Windows, macOS, and Linux.
- **Header Generation via `configure_file`:** Generation of configuration headers (`autoconf.h`, `bbconfigopts.h`, `applet_tables.h`, `NUM_APPLETS.h`, `common_bufsiz.h`, `usage.h`) into `CMAKE_BINARY_DIR`.
- **Progressive Applet Enablement:** Hundreds of applets tiered from pure computation and text filtering to advanced filesystem, editor, shell, and network applets.
- **Multiple Packaging Formats:** Generates NSIS executable installers and standalone portable ZIP archives via CPack.
- **Automated CI/CD:** Continuous synchronization and release generation via GitHub Actions.

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
   git clone https://github.com/mirror/busybox.git
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

4. Run tests:
   ```cmd
   ctest --test-dir build -C Release --output-on-failure
   ```

5. Package installers:
   ```cmd
   cd build
   cpack -C Release -G ZIP
   cpack -C Release -G NSIS
   ```

---

## Running with MSVC under Wine (macOS / Linux)

A helper script `build_msvc_wine.sh` is provided to cross-compile and test with MSVC under Wine:

```bash
./build_msvc_wine.sh
```

---

## Roadmap and Status

See [BUSYBOX_TODO_PLAN.md](BUSYBOX_TODO_PLAN.md) for the complete roadmap, tiered applet enablement checklist, MSVC compatibility shims, and testing milestones.

---

## License

This build system and integration packaging are tri-licensed under:
- [CC0 1.0 Universal](LICENSE-CC0)
- [Apache License 2.0](LICENSE-APACHE)
- [MIT License](LICENSE-MIT)

BusyBox itself is licensed under the GNU General Public License version 2 (GPL-2.0).
