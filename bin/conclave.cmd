@echo off
REM conclave launcher (Windows cmd)
set "HERE=%~dp0.."
set "CONCLAVE_HOME=%HERE%"
where python >nul 2>nul && (set "PY=python") || (set "PY=python3")
%PY% -m conclave.cli %*
