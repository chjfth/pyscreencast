@echo off
@setlocal
set batdir=%~dp0
set batdir=%batdir:~0,-1%

PATH=%batdir%\Python27

python %batdir%\pyscreencast\pyscreencast.py

rem echo %batdir%
