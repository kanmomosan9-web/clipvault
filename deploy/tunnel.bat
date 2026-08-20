@echo off
REM Opens an SSH tunnel from this PC to the ClipVault job API on the VM.
REM Replace VMHOST with your VM's public IP.
set VMHOST=ubuntu@129.225.11.4
set KEY=%USERPROFILE%\.ssh\oracle_vm

:loop
echo Connecting tunnel to %VMHOST% ...
ssh -i "%KEY%" -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 8787:127.0.0.1:8787 %VMHOST%
echo Tunnel dropped. Reconnecting in 15s...
timeout /t 15 /nobreak >nul
goto loop
