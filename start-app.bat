@echo off
rem start-app.bat - the double-clickable front door. Everything real is in
rem start-app.ps1; this exists so the app can be started without opening a
rem terminal or knowing about ExecutionPolicy.
rem
rem   start-app.bat           the app
rem   start-app.bat -Admin    the app, straight into the admin panel
rem   start-app.bat -Restart  kill whatever holds the ports, start fresh
rem
rem -ExecutionPolicy Bypass applies to THIS invocation only - it changes no
rem machine setting. Without it a freshly cloned repo cannot run its own script.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-app.ps1" %*
if errorlevel 1 pause
