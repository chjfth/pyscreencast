@echo off
@setlocal
set batdir=%~dp0
set batdir=%batdir:~0,-1%

PATH=%batdir%\Python27;%PATH%

pushd %batdir%
if not exist "Python27" (
	echo Extracting Python27...
	unzip Python27.zip
)

REM With `cmd /c`, the CMD title is restored after python process finishes.
cmd /c @title pyscreencast && python %batdir%\pyscreencast\pyscreencast.py

