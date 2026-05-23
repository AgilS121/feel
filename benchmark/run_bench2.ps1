# Re-run for Feel compiled + Go native (longer warmup).
$ErrorActionPreference = "SilentlyContinue"

function Stop-OnPort($port) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
}

function Run-Bench($name, $port, $cmd, $arglist, $n, $c) {
  Stop-OnPort $port
  Write-Host ""
  Write-Host "=== $name (port $port, n=$n c=$c) ===" -ForegroundColor Cyan
  $proc = Start-Process -FilePath $cmd -ArgumentList $arglist -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 3
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/hello" -UseBasicParsing -TimeoutSec 5
    Write-Host "  health: $($r.StatusCode) $($r.Content)" -ForegroundColor DarkGray
  } catch {
    Write-Host "  health: FAIL ($_)" -ForegroundColor Red
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Stop-OnPort $port
    return
  }
  $abOut = & ab -q -n $n -c $c "http://127.0.0.1:$port/hello" 2>&1
  $time = ($abOut | Select-String 'Time taken for tests:').ToString().Trim()
  $rps = ($abOut | Select-String 'Requests per second:').ToString().Trim()
  $tpr = ($abOut | Select-String 'Time per request:.*\(mean\)').ToString().Trim()
  $fail = ($abOut | Select-String 'Failed requests:').ToString().Trim()
  Write-Host "  $time"
  Write-Host "  $rps"
  Write-Host "  $tpr"
  Write-Host "  $fail"
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Stop-OnPort $port
}

foreach ($p in 3001,3003) { Stop-OnPort $p }

Run-Bench "Feel compiled (Go)"   3001 "D:\feel\benchmark\hello_feel.exe" @() 10000 50
Run-Bench "Go native (net/http)" 3003 "D:\feel\benchmark\hello_go.exe"   @() 10000 50

Write-Host ""
Write-Host "Done." -ForegroundColor Green
