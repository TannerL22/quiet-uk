# Double-click Launch Quiet UK.cmd in the containing project folder.
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$serverScript = Join-Path $PSScriptRoot '29_serve_explorer.py'
$releasePath = Join-Path $projectRoot 'artifacts\explorer_display_v4\dataset.json'
$appUrl = 'http://127.0.0.1:8766/'
$launchLock = New-Object System.Threading.Mutex($false, 'Local\QuietUK-Launcher-8766')
$ownsLock = $false

function Test-QuietUK {
    try {
        $record = Invoke-RestMethod ($appUrl + 'api/dataset') -TimeoutSec 2
        if (-not (Test-Path -LiteralPath $releasePath)) { return $false }
        $expected = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
        if ($record.release_id -ne $expected.release_id -or $record.display_contract_version -ne 2) { return $false }
        $pilotManifestPath = Join-Path $projectRoot 'artifacts\source_pilot_v1\manifest.json'
        $regionalManifestPath = Join-Path $projectRoot 'artifacts\source_regions_v1\manifest.json'
        if (Test-Path -LiteralPath $regionalManifestPath) { $pilotManifestPath = $regionalManifestPath }
        if (Test-Path -LiteralPath $pilotManifestPath) {
            $expectedPilot = Get-Content -LiteralPath $pilotManifestPath -Raw | ConvertFrom-Json
            $servedPilot = Invoke-RestMethod ($appUrl + 'api/pilot') -TimeoutSec 2
            if ($servedPilot.release_id -ne $expectedPilot.release_id) { return $false }
        }
        return $true
    } catch { return $false }
}

try {
    $ownsLock = $launchLock.WaitOne(0)
    if (-not $ownsLock) { exit 0 } # A simultaneous click is already starting the app.
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'The project Python environment is missing. Restore the .venv folder before launching.'
    }
    if (-not (Test-QuietUK)) {
        $probe = New-Object System.Net.Sockets.TcpClient
        try { $probe.Connect('127.0.0.1', 8766); $portBusy = $true }
        catch { $portBusy = $false }
        finally { $probe.Dispose() }
        if ($portBusy) { throw 'Port 8766 is already used by another server or an older Quiet UK release. Stop that server and try again.' }

        $logRoot = Join-Path $projectRoot 'artifacts\launcher'
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $stdoutPath = Join-Path $logRoot ($stamp + '-stdout.log')
        $stderrPath = Join-Path $logRoot ($stamp + '-stderr.log')
        $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList ('"' + $serverScript + '"') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
        $deadline = (Get-Date).AddMinutes(3)
        do {
            if (Test-QuietUK) { break }
            $serverProcess.Refresh()
            if ($serverProcess.HasExited) { throw "Quiet UK could not start. See the error log: $stderrPath" }
            if ((Get-Date) -gt $deadline) { throw "Quiet UK is taking longer than expected. It may still be preparing data. See: $stdoutPath" }
            Start-Sleep -Milliseconds 500
        } while ($true)
    }
    if (-not $NoBrowser) { Start-Process $appUrl }
    Write-Output 'Quiet UK is ready at http://127.0.0.1:8766/'
} catch {
    if ($NoBrowser) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Quiet UK could not launch', 'OK', 'Error') | Out-Null
    exit 1
} finally {
    if ($ownsLock) { $launchLock.ReleaseMutex() }
    $launchLock.Dispose()
}
