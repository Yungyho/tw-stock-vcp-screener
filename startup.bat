@echo off
REM ============================================================
REM  台股 VCP Stage 2 選股系統 — Windows 開機自動啟動腳本
REM ============================================================
REM
REM  使用方法：
REM    1. 按下 Win + R，輸入 shell:startup，按 Enter
REM    2. 將此檔案的「捷徑」拖入開啟的啟動資料夾
REM    3. 下次開機即自動在背景啟動服務
REM
REM  手動執行：直接雙擊此 .bat 檔案即可
REM ============================================================

cd /d "C:\Users\shouw\OneDrive\文件\AI Project\tw-stock-vcp-screener"
call .venv\Scripts\activate.bat
start /min cmd /c "python main.py"
