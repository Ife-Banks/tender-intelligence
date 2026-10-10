# Kill any existing uvicorn/admin processes
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*tender_intelligence.admin*" -or 
    $_.CommandLine -like "*uvicorn*"
} | Stop-Process -Force

Write-Host "Stopped existing server processes (if any)" -ForegroundColor Yellow

# Wait a moment
Start-Sleep -Seconds 2

# Start the server
Write-Host "Starting server with: uv run python -m tender_intelligence.admin" -ForegroundColor Green
& uv run python -m tender_intelligence.admin
