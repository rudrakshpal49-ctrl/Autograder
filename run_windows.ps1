# Start AutoGrader using the local virtual environment (Windows PowerShell)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.\.venv\Scripts\python.exe')) {
    Write-Error 'Virtual environment is missing. Follow the setup steps in readme.md.'
    exit 1
}
if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) {
    Write-Error 'GCC is not on PATH. See the MSYS2 instructions in readme.md.'
    exit 1
}
& .\.venv\Scripts\python.exe -m streamlit run app.py
