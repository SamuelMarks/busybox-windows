#!/bin/sh
set -e

SRC_DIR="$PWD"
BUILD_TYPE="Debug"
if [ -z "$PARALLEL_JOBS" ]; then
    PARALLEL_JOBS="4"
fi

if [ -z "$MSVC_WINE_PATH" ]; then
    if [ -d "$HOME/my_msvc/bin/x64" ]; then
        MSVC_WINE_PATH="$HOME/my_msvc"
    elif [ -d "$HOME/my_msvc/opt/msvc/bin/x64" ]; then
        MSVC_WINE_PATH="$HOME/my_msvc/opt/msvc"
    elif [ -d "/opt/msvc/bin/x64" ]; then
        MSVC_WINE_PATH="/opt/msvc"
    else
        MSVC_WINE_PATH="$HOME/my_msvc"
    fi
fi

export PATH="${MSVC_WINE_PATH}/bin/x64:$PATH"
export MVK_CONFIG_LOG_LEVEL=0
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine_cdd_c}"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml="
export WINE_AUTO_INSTALL=0

echo "Starting wineserver..."
if command -v wineserver >/dev/null 2>&1; then
    wineserver -p >/dev/null 2>&1 || true
    wine wineboot || true
    wine reg add "HKCU\\Software\\Wine\\WineDbg" /v ShowCrashDialog /t REG_DWORD /d 0 /f >/dev/null 2>&1 || true
    wine reg add "HKCU\\Software\\Wine\\WineDbg" /v DontShowGui /t REG_DWORD /d 1 /f >/dev/null 2>&1 || true
else
    echo "Warning: wineserver not found. Skipping wine initialization."
fi

FETCH_ARGS=""
if [ -d "../auto-win-msvc" ]; then
    FETCH_ARGS="$FETCH_ARGS -DFETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC=\"${SRC_DIR}/../auto-win-msvc\" -DFETCHCONTENT_SOURCE_DIR_AUTO-WIN-MSVC=\"${SRC_DIR}/../auto-win-msvc\""
fi
if [ -d "../busybox" ]; then
    FETCH_ARGS="$FETCH_ARGS -DBUSYBOX_SOURCE_DIR=\"${SRC_DIR}/../busybox\""
fi

echo "======================================================================"
echo "MSVC-Wine | Shared Lib (MDd) | LTO OFF | Multi-thread"
echo "======================================================================"
BUILD_DIR="${SRC_DIR}/build_msvc_wine_shared"
eval cmake -S "\"${SRC_DIR}\"" -B "\"${BUILD_DIR}\"" -DCMAKE_BUILD_TYPE="\"${BUILD_TYPE}\"" \
  -DCMAKE_SYSTEM_NAME=Windows \
  -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl \
  -DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded \
  -DCMAKE_CROSSCOMPILING_EMULATOR=wine \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF \
  -DBUILD_TESTING=ON \
  -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDebugDLL $FETCH_ARGS "$@"

cmake --build "${BUILD_DIR}" --config "${BUILD_TYPE}" --parallel ${PARALLEL_JOBS}

cd "${BUILD_DIR}"
EXTRA_WINEPATH=""
if [ -d "_deps" ]; then
    for dep in _deps/*-build; do
        if [ -d "$dep" ]; then
            EXTRA_WINEPATH="${EXTRA_WINEPATH};${BUILD_DIR}/${dep}"
        fi
    done
fi
export WINEPATH="${BUILD_DIR}${EXTRA_WINEPATH};${MSVC_WINE_PATH}/bin/x64;${MSVC_WINE_PATH}/VC/Redist/MSVC/14.51.36231/debug_nonredist/x64/Microsoft.VC145.DebugCRT;${MSVC_WINE_PATH}/Windows Kits/10/bin/10.0.26100.0/x64/ucrt"
ctest -C "${BUILD_TYPE}" --output-on-failure --timeout 180
cd "${SRC_DIR}"

echo "======================================================================"
echo "MSVC-Wine | Static Lib (MTd) | LTO OFF | Multi-thread"
echo "======================================================================"
BUILD_DIR="${SRC_DIR}/build_msvc_wine_static"
eval cmake -S "\"${SRC_DIR}\"" -B "\"${BUILD_DIR}\"" -DCMAKE_BUILD_TYPE="\"${BUILD_TYPE}\"" \
  -DCMAKE_SYSTEM_NAME=Windows \
  -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl \
  -DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded \
  -DCMAKE_CROSSCOMPILING_EMULATOR=wine \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF \
  -DBUILD_TESTING=ON \
  -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDebug $FETCH_ARGS "$@"

cmake --build "${BUILD_DIR}" --config "${BUILD_TYPE}" --parallel ${PARALLEL_JOBS}

cd "${BUILD_DIR}"
EXTRA_WINEPATH=""
if [ -d "_deps" ]; then
    for dep in _deps/*-build; do
        if [ -d "$dep" ]; then
            EXTRA_WINEPATH="${EXTRA_WINEPATH};${BUILD_DIR}/${dep}"
        fi
    done
fi
export WINEPATH="${BUILD_DIR}${EXTRA_WINEPATH};${MSVC_WINE_PATH}/bin/x64;${MSVC_WINE_PATH}/VC/Redist/MSVC/14.51.36231/debug_nonredist/x64/Microsoft.VC145.DebugCRT;${MSVC_WINE_PATH}/Windows Kits/10/bin/10.0.26100.0/x64/ucrt"
ctest -C "${BUILD_TYPE}" --output-on-failure --timeout 180
cd "${SRC_DIR}"

echo "All BusyBox MSVC-Wine variations completed successfully."
