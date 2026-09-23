@echo off
:: ═══════════════════════════════════════════════════════
::  ARIA-CIL — CMPDI Document Intelligence Platform
::  Quick startup script for Windows
:: ═══════════════════════════════════════════════════════
title ARIA-CIL Platform

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║   ARIA-CIL · CMPDI/CIL Document Intelligence    ║
echo  ║   Sovereign On-Premise AI Platform               ║
echo  ╚══════════════════════════════════════════════════╝
echo.

:: Check Ollama
echo [1/3] Checking Ollama service...
ollama list >nul 2>&1
if errorlevel 1 (
    echo  ⚠  Ollama not found or not running.
    echo     Please install from https://ollama.com and run: ollama serve
    echo.
) else (
    echo  ✓  Ollama is available
    echo.
    echo [2/3] Pulling required models if not present...
    ollama pull llama3.2 >nul 2>&1
    ollama pull nomic-embed-text >nul 2>&1
    echo  ✓  Models ready
)

echo.
echo [3/3] Starting ARIA-CIL server on http://localhost:5000 ...
echo       Open index.html in your browser to access the dashboard.
echo       Press Ctrl+C to stop.
echo.

python server.py
pause
