@echo off
chcp 65001 >nul
title AI Bridge — Build

echo.
echo  ============================================
echo   AI Bridge — Build para Windows
echo  ============================================
echo.

:: Verifica Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado.
    echo Instale em: https://www.python.org/downloads/
    echo Marque "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

echo [OK] Python encontrado:
python --version
echo.

:: Instala dependências
echo [1/3] Instalando dependencias...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas.
echo.

:: Compila
echo [2/3] Compilando AI-Bridge.exe ...
python -m PyInstaller build.spec --noconfirm
if errorlevel 1 (
    echo [ERRO] Falha na compilacao.
    pause
    exit /b 1
)
echo.

:: Verifica resultado
if not exist "dist\AI-Bridge.exe" (
    echo [ERRO] AI-Bridge.exe nao foi gerado.
    pause
    exit /b 1
)

echo [3/3] Pronto!
echo.
echo  Arquivo gerado: dist\AI-Bridge.exe
echo  Tamanho:
for %%F in ("dist\AI-Bridge.exe") do echo    %%~zF bytes
echo.
echo Abrindo pasta dist...
explorer dist
echo.
pause
