<#
    Friskoli-CAD 本地启动脚本

    用法:
        .\start.ps1                 # 默认端口 8765，自动打开浏览器
        .\start.ps1 -Port 9000      # 换端口
        .\start.ps1 -SyncOnly       # 只用旧的同步接口，不启用异步任务服务
        .\start.ps1 -NoBrowser      # 不自动打开浏览器

    按 Ctrl+C 停止服务。
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

# 发一个 HTTP GET，只在服务真的返回 200 时判为已就绪（不依赖 System.Net.Http）
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

# 1. 选解释器：优先项目内 .venv，否则用 PATH 上的 python
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $python = $venvPython
}
else {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { throw "找不到 Python。请安装 Python 3.11+，或在项目里创建 .venv。" }
    $python = $found.Source
}

# 2. 检查依赖，缺了就装（需要网络）
$checkScript = "import importlib.util as u; print(' '.join(m for m in ('jsonschema', 'numpy', 'rfc8785', 'psutil') if u.find_spec(m) is None))"
$missing = ([string](& $python -c $checkScript)).Trim()
if ($missing) {
    Write-Host "缺少依赖: $missing，正在安装 ..." -ForegroundColor Yellow
    & $python -m pip install -e $root
    $missing = ([string](& $python -c $checkScript)).Trim()
    if ($missing) {
        throw "依赖仍然缺失: $missing`n请手动执行: `"$python`" -m pip install -e `"$root`""
    }
}

# 3. 端口占用检查
if (Test-Capabilities -TargetPort $Port) {
    Write-Host "Friskoli-CAD 已经在运行: $url" -ForegroundColor Yellow
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
}

$inUse = $false
$probe = New-Object System.Net.Sockets.TcpClient
try { $probe.Connect('127.0.0.1', $Port); $inUse = $true } catch { } finally { $probe.Dispose() }
if ($inUse) {
    Write-Host "端口 $Port 已被其他程序占用。换一个端口: .\start.ps1 -Port 8766" -ForegroundColor Red
    exit 1
}

# 4. 启动服务（从源码运行，所以设置 PYTHONPATH）
$env:PYTHONPATH = Join-Path $root 'src'
$serverArgs = @('-m', 'friskoli_cad.replay_service', '--port', "$Port")
if ($SyncOnly) { $serverArgs += '--sync-only' }

Write-Host "启动 Friskoli-CAD  (python: $python)" -ForegroundColor Cyan
$proc = Start-Process -FilePath $python -ArgumentList $serverArgs -NoNewWindow -PassThru

try {
    # 5. 等接口响应，确认服务真的起来了
    $ready = $false
    foreach ($attempt in 1..80) {
        if ($proc.HasExited) { throw "服务进程已退出（退出码 $($proc.ExitCode)），原因见上方输出。" }
        if (Test-Capabilities -TargetPort $Port) { $ready = $true; break }
        Start-Sleep -Milliseconds 250
    }
    if ($proc.HasExited) { throw "服务进程已退出（退出码 $($proc.ExitCode)），原因见上方输出。" }
    if (-not $ready) { throw "等待服务就绪超时（$url）。" }

    Write-Host "已就绪: $url   按 Ctrl+C 停止" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $url }

    Wait-Process -Id $proc.Id
}
finally {
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "服务已停止。" -ForegroundColor DarkGray
    }
}
