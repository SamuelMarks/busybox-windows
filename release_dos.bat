@echo off
REM ===========================================================================
REM release_dos.bat - OpenWatcom 32-bit DOS Build & Packaging Script for BusyBox
REM ===========================================================================

setlocal enabledelayedexpansion

if not defined WATCOM (
    if exist "C:\usr\WATCOM\h" (
        set "WATCOM=C:\usr\WATCOM"
    ) else if exist "C:\WATCOM\h" (
        set "WATCOM=C:\WATCOM"
    ) else (
        echo Error: OpenWatcom not found at C:\usr\WATCOM or C:\WATCOM
        exit /b 1
    )
)

set "WATCOM_BIN=%WATCOM%\binnt64"
if not exist "%WATCOM_BIN%\wcl386.exe" (
    set "WATCOM_BIN=%WATCOM%\binnt"
)
if not exist "%WATCOM_BIN%\wcl386.exe" (
    echo Error: wcl386.exe not found in %WATCOM%\binnt64 or %WATCOM%\binnt
    exit /b 1
)

set "PATH=%WATCOM_BIN%;%WATCOM%\binw;%PATH%"
set "INCLUDE=%WATCOM%\h"
set "EDPATH=%WATCOM%\eddat"
set "WIPFC=%WATCOM%\wipfc"
set "WATCOM_FWD=%WATCOM:\=/%"
set "WCL386_FWD=%WATCOM_BIN:\=/%/wcl386.exe"

set CMAKE_EXTRA_ARGS=
if exist ..\auto-win-msvc\CMakeLists.txt (
    set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DFETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC=..\auto-win-msvc
) else if exist auto-win-msvc\CMakeLists.txt (
    set CMAKE_EXTRA_ARGS=!CMAKE_EXTRA_ARGS! -DFETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC=auto-win-msvc
)

set BUILD_DIR=build_dos
if exist %BUILD_DIR% rmdir /s /q %BUILD_DIR%

echo Configuring 32-bit Protected-Mode DOS build with OpenWatcom...
cmake -G "Watcom WMake" -B %BUILD_DIR% -S . ^
  -DCMAKE_SYSTEM_NAME=DOS ^
  -DCMAKE_SYSTEM_PROCESSOR=I386 ^
  -DCMAKE_C_COMPILER="!WCL386_FWD!" ^
  -DCMAKE_C_FLAGS_INIT="-bt=dos -mf -3r -zq" ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DBUILD_SHARED_LIBS=OFF ^
  -DBUILD_TESTING=OFF ^
  !CMAKE_EXTRA_ARGS!
if errorlevel 1 exit /b 1

echo Compiling BUSYBOX.EXE for 32-bit DOS...
cmake --build %BUILD_DIR% --config Release
if errorlevel 1 exit /b 1

echo Packaging DOS ZIP archive...
cd %BUILD_DIR%
cpack -C Release -G ZIP -D CPACK_PACKAGE_FILE_NAME=busybox-1.39.0-dos32
copy /y busybox.exe busybox-1.39.0-dos32.exe >nul
cd ..

echo OpenWatcom 32-bit DOS build complete!
