# ============================================================
# ArogyaVoice - Local Development Runner
# Starts Backend (port 8000), Frontend (port 5173), and ngrok
# ============================================================

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Starting ArogyaVoice Voice-First System        " -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

# 1. Start Backend in a separate window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\backend'; & .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --port 8000"

# 2. Start Frontend in a separate window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\frontend'; npm run dev"

# 3. Start ngrok in a separate window with your reserved domain
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& 'C:\Users\satis\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe' http 8000 --url https://sprite-cultivate-congrats.ngrok-free.dev"

Write-Host "`nAll 3 services launched in separate windows!" -ForegroundColor Green
Write-Host "Backend:  http://localhost:8000" -ForegroundColor Yellow
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Yellow
Write-Host "ngrok:    Check the ngrok window for your https://xxxx.ngrok-free.app URL" -ForegroundColor Yellow
Write-Host "`nNext Step: Copy the ngrok URL and paste it in Twilio 'Receive a call' as described in Phase 5 guide." -ForegroundColor Cyan
