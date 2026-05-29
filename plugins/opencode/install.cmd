@echo off
setlocal

rem Windows source installer launcher for the memsearch OpenCode plugin.
rem Prefer Git Bash explicitly so plain "bash" does not resolve to WSL.

set "SCRIPT_DIR=%~dp0"
set "GIT_BASH="

if exist "%ProgramFiles%\Git\bin\bash.exe" set "GIT_BASH=%ProgramFiles%\Git\bin\bash.exe"

if not defined GIT_BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "GIT_BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"

if not defined GIT_BASH (
  for /f "delims=" %%G in ('where git.exe 2^>nul') do (
    if not defined GIT_BASH (
      for %%B in ("%%~dpG..\bin\bash.exe") do (
        if exist "%%~fB" set "GIT_BASH=%%~fB"
      )
    )
  )
)

if not defined GIT_BASH (
  echo [ERROR] Git Bash was not found.
  echo         Install Git for Windows, then rerun:
  echo         plugins\opencode\install.cmd
  exit /b 1
)

if /i "%~1"=="--print-git-bash" (
  echo %GIT_BASH%
  exit /b 0
)

"%GIT_BASH%" --login "%SCRIPT_DIR%install.sh" %*
exit /b %ERRORLEVEL%
