chcp 65001 >nul
@echo off
REM =====================================================================
REM  M4: start_assistant.bat  —  一键启动 助手 Agent + 助手语音
REM  严格顺序：TTS → 等待 ready → Agent（绝不抢跑）
REM  三项保护：
REM    ① 启动超时：等 600s 仍不 ready → 明确报错，Agent 以纯文字模式启动
REM    ② 已运行则复用：9880 已在线就直接复用，不启第二个 GPT-SoVITS
REM    ③ Agent 退出不杀 TTS：TTS / Agent 各自独立窗口，关 Agent 不影响 TTS
REM  依赖：tools\tts_health.py（仅标准库，任意 Python 可跑）
REM  注意：路径含空格会破坏 start 的引号，请将 GPT-SoVITS / assistant-agent
REM        放在【无空格】路径下（当前默认路径均满足）。
REM =====================================================================

setlocal EnableDelayedExpansion
title Assistant Launcher (M4)

REM ---------- 可配置项（如路径变动改这里即可） ----------
set "GPT_DIR=<GPT_SOVITS_DIR>"
set "GPT_PY=%GPT_DIR%\runtime\python.exe"
set "AGENT_DIR=<ASSISTANT_AGENT_DIR>"
set "AGENT_PY=%AGENT_DIR%\.venv\Scripts\python.exe"
set "TTS_HOST=127.0.0.1"
set "TTS_PORT=9880"
set "TIMEOUT_SEC=600"
set "TTS_CFG=GPT_SoVITS\configs\tts_infer.yaml"

echo ============================================================
echo   Assistant Launcher ^(M4^)  —  Agent + Voice
echo ============================================================
echo.

REM ============ ① 环境检查 ============
echo [*] Environment checks...
set "CHK_FAIL=0"

if not exist "%GPT_DIR%"        (echo   [X] GPT-SoVITS dir missing: %GPT_DIR%        & set CHK_FAIL=1)
if not exist "%GPT_PY%"        (echo   [X] GPT-SoVITS runtime python missing        & set CHK_FAIL=1)
if not exist "%GPT_DIR%\api_v2.py"   (echo   [X] api_v2.py missing                       & set CHK_FAIL=1)
if not exist "%GPT_DIR%\%TTS_CFG%"  (echo   [X] tts_infer.yaml missing                  & set CHK_FAIL=1)
if not exist "%AGENT_DIR%"     (echo   [X] Assistant Agent dir missing: %AGENT_DIR%    & set CHK_FAIL=1)
if not exist "%AGENT_PY%"      (echo   [X] Agent venv python missing                 & set CHK_FAIL=1)
if not exist "%AGENT_DIR%\ui\app.py" (echo   [X] ui/app.py missing                    & set CHK_FAIL=1)
if not exist "%AGENT_DIR%\tools\tts_health.py" (echo   [X] tools/tts_health.py missing        & set CHK_FAIL=1)

REM flet 是否装进 Agent venv（Agent 启动强依赖）
"%AGENT_PY%" -c "import flet" >nul 2>&1
if errorlevel 1 (echo   [X] flet not installed in Agent venv          & set CHK_FAIL=1)

if "%CHK_FAIL%"=="1" (
    echo.
    echo [FATAL] Environment checks failed. Aborting.
    pause
    exit /b 1
)
echo   [OK] Environment checks passed.
echo.

REM ============ ② 复用 or 启动 TTS ============
echo [*] Checking if TTS already running on :%TTS_PORT%...
"%AGENT_PY%" "%AGENT_DIR%\tools\tts_health.py" --host %TTS_HOST% --port %TTS_PORT% --timeout 2 --interval 1
if "%errorlevel%"=="0" (
    echo   [OK] TTS already running on :%TTS_PORT% -- reusing.
    goto start_agent
)

echo [*] Starting GPT-SoVITS TTS API in a background window...
REM 使用 start /D 设置工作目录，避免 cmd /k 嵌套引号与 ^&^& 转义问题。
start "GPT-SoVITS TTS" /D "%GPT_DIR%" cmd /k "if not exist logs mkdir logs && %GPT_PY% api_v2.py -a %TTS_HOST% -p %TTS_PORT% -c %TTS_CFG% > logs\tts_api.log 2>&1"

REM ============ ③/④ 等待 API ready（超时 600s） ============
:wait_ready
echo [*] Waiting for TTS to be ready (timeout %TIMEOUT_SEC%s)...
"%AGENT_PY%" "%AGENT_DIR%\tools\tts_health.py" --host %TTS_HOST% --port %TTS_PORT% --timeout %TIMEOUT_SEC% --interval 2
if "%errorlevel%"=="0" (
    echo   [OK] GPT-SoVITS TTS ready on :%TTS_PORT%.
) else (
    echo   [WARN] GPT-SoVITS TTS did not become ready within %TIMEOUT_SEC%s.
    echo   [WARN] Assistant Agent will start in TEXT-ONLY mode（voice disabled）. 失败不致命。
)

REM ============ ⑤ 启动 Agent ============
:start_agent
echo [*] Starting Assistant Agent...
REM 直接用 start /D 运行 Agent python，不经过 cmd /k，彻底避免引号嵌套。
start "Assistant Agent" /D "%AGENT_DIR%" "%AGENT_PY%" "ui/app.py"

echo.
echo ============================================================
echo   Assistant Agent is launching in a new window.
echo   - TTS runs in its own window; it stays after Agent closes.
echo   - Re-running this bat reuses the live TTS (won't start a 2nd).
echo   - Closing this window won't affect TTS / Agent.
echo ============================================================
pause
endlocal
exit /b 0
