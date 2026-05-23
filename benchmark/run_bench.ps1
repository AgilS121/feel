# Benchmark runner — small load for interpreter, full for compiled.
$ErrorActionPreference = "SilentlyContinue"

function Stop-OnPort($port) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Milliseconds 300
}

function Run-Bench($name, $port, $cmd, $arglist, $n, $c, $warmupSecs = 1) {
  Stop-OnPort $port
  Write-Host ""
  Write-Host "=== $name (port $port, n=$n c=$c) ===" -ForegroundColor Cyan
  $proc = Start-Process -FilePath $cmd -ArgumentList $arglist -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds $warmupSecs
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
  $rps = ($abOut | Select-String 'Requests per second:').ToString().Trim()
  $tpr = ($abOut | Select-String 'Time per request:.*\(mean\)').ToString().Trim()
  $fail = ($abOut | Select-String 'Failed requests:').ToString().Trim()
  $time = ($abOut | Select-String 'Time taken for tests:').ToString().Trim()
  Write-Host "  $time"
  Write-Host "  $rps"
  Write-Host "  $tpr"
  Write-Host "  $fail"
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Stop-OnPort $port
}

foreach ($p in 3001,3003,3004,3005) { Stop-OnPort $p }

# Feel interpreter — tiny load (it's tree-walking Python)
Run-Bench "Feel interpreter"        3001 "python" @("D:\feel\main.py","run","D:\feel\benchmark\hello.feel") 500 10 2

# Feel compiled (Go runtime)
Run-Bench "Feel compiled (Go)"      3001 "D:\feel\benchmark\hello_feel.exe" @() 10000 50 1

# Go native
Run-Bench "Go native (net/http)"    3003 "D:\feel\benchmark\hello_go.exe" @() 10000 50 1

# Python stdlib
Run-Bench "Python stdlib http.server" 3004 "python" @("D:\feel\benchmark\hello.py") 2000 10 1

# PHP built-in
Run-Bench "PHP built-in server"     3005 "php" @("-S","127.0.0.1:3005","D:\feel\benchmark\hello.php") 2000 10 1

Write-Host ""
Write-Host "Done." -ForegroundColor Green
