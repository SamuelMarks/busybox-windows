@echo off
REM ===========================================================================
REM release.bat - Native Windows MSVC Build, Test, and Packaging Script
REM
REM Configures, compiles, tests, and packages BusyBox for Windows using CMake,
REM CTest, and CPack (ZIP and NSIS). Automatically detects local sibling
REM checkouts of ..\busybox and ..\auto-win-msvc when present.
REM
REM Arguments:
REM   %~1 (BUSYBOX_REF) - Optional BusyBox git branch or tag (defaults to master).
REM ===========================================================================

setlocal enabledelayedexpansion

set BUSYBOX_REF=%~1
if "%BUSYBOX_REF%"=="" set BUSYBOX_REF=master

echo ==============================================
echo Building BusyBox Windows Release (%BUSYBOX_REF%)
echo ==============================================

set CMAKE_EXTRA_ARGS=

if not exist busybox (
    if exist ..\busybox\include\busybox.h (
        echo Using local ..\busybox repository...
        set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DBUSYBOX_SOURCE_DIR=..\busybox
    ) else (
        echo Cloning BusyBox from https://github.com/mirror/busybox.git...
        git clone --branch %BUSYBOX_REF% --depth 1 https://github.com/mirror/busybox.git busybox
        set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DBUSYBOX_SOURCE_DIR=busybox
    )
) else (
    set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DBUSYBOX_SOURCE_DIR=busybox
)

if not exist auto-win-msvc (
    if exist ..\auto-win-msvc\CMakeLists.txt (
        echo Using local ..\auto-win-msvc repository...
        set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DFETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC=..\auto-win-msvc
    )
) else (
    set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DFETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC=auto-win-msvc
)

set BUILD_DIR=build_release
if exist %BUILD_DIR% rmdir /s /q %BUILD_DIR%

echo Configuring with CMake...
cmake -B %BUILD_DIR% -S . -DCMAKE_BUILD_TYPE=Release !CMAKE_EXTRA_ARGS!
if errorlevel 1 exit /b 1

echo Compiling BusyBox Release...
cmake --build %BUILD_DIR% --config Release
if errorlevel 1 exit /b 1

echo Testing BusyBox...
ctest --test-dir %BUILD_DIR% -C Release --output-on-failure
if errorlevel 1 exit /b 1

echo Packaging with CPack...
cd %BUILD_DIR%
cpack -C Release -G ZIP
cpack -C Release -G NSIS
cd ..

echo Build and packaging complete!
