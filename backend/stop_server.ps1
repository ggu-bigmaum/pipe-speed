# server.pid / server.launcher.pid 에 기록된(=이 스크립트 세트가 띄운) PID 만 종료.
# 명령줄에 backend\app.py 가 없으면 종료하지 않음.
foreach ($n in "server.pid","server.launcher.pid") {
  $f = Join-Path $PSScriptRoot $n
  if (-not (Test-Path $f)) { continue }
  $id = [int](Get-Content $f)
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
  if ($proc -and $proc.CommandLine -like "*app.py*" -and $proc.CommandLine -like "*backend*") {
    Stop-Process -Id $id; Write-Output "STOPPED PID $id"
  } else { Write-Output "PID $id is not our server (or already gone); nothing killed" }
  Remove-Item $f
}
