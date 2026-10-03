@echo off
setlocal enabledelayedexpansion

set BUSYBOX_REF=%~1
if "%BUSYBOX_REF%"=="" set BUSYBOX_REF=master

echo ==============================================
echo Building BusyBox Windows Release (%BUSYBOX_REF%)
echo ==============================================

if not exist busybox (
    if exist ..\busybox\include\busybox.h (
        echo Using local ..\busybox repository...
    ) else (
        echo Cloning BusyBox from https://github.com/mirror/busybox.git...
        git clone --branch %BUSYBOX_REF% --depth 1 https://github.com/mirror/busybox.git busybox
    )
)

if not exist auto-win-msvc (
    if exist ..\auto-win-msvc\CMakeLists.txt (
        echo Using local ..\auto-win-msvc repository...
    )
)

set BUILD_DIR=build_release
if exist %BUILD_DIR% rmdir /s /q %BUILD_DIR%

echo Configuring with CMake...
cmake -B %BUILD_DIR% -S . -DCMAKE_BUILD_TYPE=Release
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
