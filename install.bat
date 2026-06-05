@echo off
:: install.bat — CTR Rosales QC (v1.0.0)
::
:: Thin double-click launcher for Windows users who cannot run PowerShell scripts
:: directly due to the default execution policy. Invokes install.ps1 with a
:: per-process ExecutionPolicy bypass — no machine-level policy change required.
::
:: Usage (File Explorer): double-click this file.
:: Usage (cmd.exe):       install.bat [args...]
::   install.bat              -- build + start
::   install.bat -Stop        -- stop the app
::   install.bat -Logs        -- follow live logs

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
