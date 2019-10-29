@echo off
@setlocal
set batdir=%~dp0
set batdir=%batdir:~0,-1%

PATH=%batdir%\Python27

pushd %batdir%
if not exist "Python27" (
	echo Extracting Python27...
	unzip Python27.zip
)

python %batdir%\pyscreencast\pyscreencast.py

rem echo %batdir%
