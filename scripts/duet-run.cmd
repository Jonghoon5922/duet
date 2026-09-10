@echo off
rem Run Duet without any launcher. uv trampolines (duet.exe, .venv python.exe)
rem break in shells where AppData is virtualized (Claude Desktop terminal, admin shell).
rem Keep this file ASCII only: cmd.exe reads batch files in the OEM code page.
set "PY=C:\Users\NB-24062008\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
rem Dependencies live inside the project dir, not under AppData: the Claude Desktop
rem terminal sees a virtualized AppData where the uv tool env does not exist.
set "SP=C:\project\duet\.duet-env\Lib\site-packages"
if not exist "%PY%" (
  echo [duet-run] python not found: %PY%
  exit /b 1
)
if not exist "%SP%\typer" (
  echo [duet-run] dependencies not visible from this shell: %SP%
  exit /b 1
)
rem pywin32 adds three dirs via a .pth file; PYTHONPATH skips .pth, so add them here.
set "PYTHONPATH=C:\project\duet\src;%SP%;%SP%\win32;%SP%\win32\lib;%SP%\pythonwin"
set "PYTHONIOENCODING=utf-8"
"%PY%" -m duet %*
