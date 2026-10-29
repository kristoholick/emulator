@echo off
rem Builds shadPS4 (Clang x64 Release) with the Visual Studio Build Tools' Clang, CMake and Ninja.
rem Output: Build\x64-Clang-Release\shadps4.exe
setlocal

for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationPath`) do set "VS=%%i"
if not defined VS (
  echo Visual Studio Build Tools not found.
  exit /b 1
)

call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
set "PATH=%VS%\VC\Tools\Llvm\x64\bin;%PATH%"

cd /d "%~dp0.."
cmake --preset x64-Clang-Release || exit /b 1
rem Clang can use 1-2 GB per job and this PC has ~12 GB of usable RAM. If the build fails with
rem "LLVM ERROR: out of memory", close other apps or lower the jobs:  set MOCHI_JOBS=3
if not defined MOCHI_JOBS set MOCHI_JOBS=6
cmake --build Build\x64-Clang-Release --parallel %MOCHI_JOBS% || exit /b 1

echo.
echo Built: %CD%\Build\x64-Clang-Release\shadps4.exe
