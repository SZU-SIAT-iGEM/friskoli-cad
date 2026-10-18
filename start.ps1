<#
    Friskoli-CAD local launcher

    Usage:
        .\start.ps1                 # Port 8765; open the browser
        .\start.ps1 -Port 9000      # Use a different port
        .\start.ps1 -SyncOnly       # Use the bounded synchronous API without the asynchronous task service
        .\start.ps1 -NoBrowser      # Leave browser opening to the user

    Press Ctrl+C to stop the service.
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$SyncOnly,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$url = "http://127.0.0.1:$Port"

# Check readiness with an HTTP GET; require status 200 without System.Net.Http.
function Test-Capabilities {
    param([int]$TargetPort)

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $TargetPort)
        $client.ReceiveTimeout = 3000
        $stream = $client.GetStream()
        $request = "GET /api/capabilities HTTP/1.1`r`nHost: 127.0.0.1:$TargetPort`r`nConnection: close`r`n`r`n"
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($request)
        $stream.Write($bytes, 0, $bytes.Length)
        $buffer = New-Object byte[] 256
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -le 0) { return $false }
        return ([System.Text.Encoding]::ASCII.GetString($buffer, 0, $read) -match '^HTTP/1\.[01] 200')
    }
    catch { return $false }
    finally { $client.Dispose() }
}

# Prefer the project-local .venv, then Python on PATH.
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $python = $venvPython
}
else {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { throw "Python was not found. Install Python 3.11+ or create a project-local .venv." }
    $python = $found.Source
}

# Check dependencies and install missing packages (network required).
$checkScript = "import importlib.util as u; print(' '.join(m for m in ('jsonschema', 'numpy', 'rfc8785', 'psutil') if u.find_spec(m) is None))"
function Get-MissingDependencies {
    # @() keeps this a string when the interpreter writes nothing to stdout: under
    # $ErrorActionPreference = 'Stop' a bare [string](...) is $null, not ''.
    $missing = (@(& $python -c $checkScript) -join ' ').Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Python could not run the dependency check.`nRun manually: `"$python`" -m pip install -e `"$root`""
    }
    return $missing
}
$missing = Get-MissingDependencies
if ($missing) {
    Write-Host "Missing dependencies: $missing. Installing ..." -ForegroundColor Yellow
    & $python -m pip install -e $root
    $missing = Get-MissingDependencies
    if ($missing) {
        throw "Dependencies are still missing: $missing`nRun manually: `"$python`" -m pip install -e `"$root`""
    }
}

# Check whether the port is already in use.
if (Test-Capabilities -TargetPort $Port) {
    Write-Host "Friskoli-CAD is already running: $url" -ForegroundColor Yellow
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
}

$inUse = $false
$probe = New-Object System.Net.Sockets.TcpClient
try { $probe.Connect('127.0.0.1', $Port); $inUse = $true } catch { } finally { $probe.Dispose() }
if ($inUse) {
    Write-Host "Port $Port is used by another application. Choose another port: .\start.ps1 -Port 8766" -ForegroundColor Red
    exit 1
}

# Start the service from this checkout by setting PYTHONPATH.
$env:PYTHONPATH = Join-Path $root 'src'
$serverArgs = @('-m', 'friskoli_cad.replay_service', '--port', "$Port")
if ($SyncOnly) { $serverArgs += '--sync-only' }

Write-Host "Starting Friskoli-CAD  (python: $python)" -ForegroundColor Cyan
$proc = Start-Process -FilePath $python -ArgumentList $serverArgs -NoNewWindow -PassThru

try {
    # Wait for the API to confirm service readiness.
    $ready = $false
    foreach ($attempt in 1..80) {
        if ($proc.HasExited) { throw "The service exited (code $($proc.ExitCode)). See the output above." }
        if (Test-Capabilities -TargetPort $Port) { $ready = $true; break }
        Start-Sleep -Milliseconds 250
    }
    if ($proc.HasExited) { throw "The service exited (code $($proc.ExitCode)). See the output above." }
    if (-not $ready) { throw "Timed out waiting for service readiness ($url)." }

    Write-Host "Ready: $url   Press Ctrl+C to stop" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $url }

    Wait-Process -Id $proc.Id
}
finally {
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "Service stopped." -ForegroundColor DarkGray
    }
}
