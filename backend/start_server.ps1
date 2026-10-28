# 백엔드 기동 (PowerShell, UTF-8). 자기가 띄운 PID만 backend\server.pid 에 기록.
# 포트 8000 이 이미 점유 중이면 기동하지 않고 점유 PID 를 알려준다(다른 프로세스는 종료하지 않음).
param([int]$Port = 8000)
$env:PYTHONUTF8 = "1"
$env:BACKEND_PORT = "$Port"
$here = $PSScriptRoot
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) { Write-Output "PORT $Port BUSY by PID $($busy.OwningProcess -join ',')"; exit 1 }
$py = Join-Path $here ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force (Join-Path $here "logs") | Out-Null
$p = Start-Process -FilePath $py -ArgumentList @("-I", ('"' + (Join-Path $here "app.py") + '"')) -WorkingDirectory $here `
  -RedirectStandardOutput (Join-Path $here "logs\server.out.log") -RedirectStandardError (Join-Path $here "logs\server.err.log") `
  -WindowStyle Hidden -PassThru
Set-Content -Path (Join-Path $here "server.launcher.pid") -Value $p.Id
Start-Sleep -Seconds 4   # app.py 가 자기 PID 를 server.pid 에 기록(venv 런처와 PID 가 다를 수 있음)
Write-Output "STARTED launcher PID $($p.Id), app PID $(Get-Content (Join-Path $here 'server.pid')) port $Port"
