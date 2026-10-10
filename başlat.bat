@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Python bul
set PYTHON_EXE=

if exist "%USERPROFILE%\miniconda3\python.exe"  set PYTHON_EXE=%USERPROFILE%\miniconda3\python.exe
if exist "%USERPROFILE%\anaconda3\python.exe"   set PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe
if exist "C:\ProgramData\miniconda3\python.exe" set PYTHON_EXE=C:\ProgramData\miniconda3\python.exe
if exist "C:\ProgramData\anaconda3\python.exe"  set PYTHON_EXE=C:\ProgramData\anaconda3\python.exe

if "%PYTHON_EXE%"=="" (
    where python >nul 2>&1
    if not errorlevel 1 set PYTHON_EXE=python
)
if "%PYTHON_EXE%"=="" (
    where py >nul 2>&1
    if not errorlevel 1 set PYTHON_EXE=py
)

if "%PYTHON_EXE%"=="" (
    echo [HATA] Python bulunamadi. https://www.anaconda.com/download
    pause
    exit /b 1
)

if not exist "%~dp0logs" mkdir "%~dp0logs"

:: pythonw.exe varsa onu kullan (konsol penceresi acmaz)
set PYTHONW_EXE=%PYTHON_EXE:python.exe=pythonw.exe%
if not exist "%PYTHONW_EXE%" set PYTHONW_EXE=

if not "%PYTHONW_EXE%"=="" (
    start "" "%PYTHONW_EXE%" "%~dp0panel.py"
) else (
    start "" /b "%PYTHON_EXE%" "%~dp0panel.py"
)

