param(
    [string]$BindIP = '192.168.0.250',
    [string]$SensorIP = '192.168.0.1',
    [ValidateRange(1,65535)][int]$Port = 2115,
    [ValidateRange(1,3600)][int]$Seconds = 30
)
$ErrorActionPreference = 'Stop'
$exe = "$PSScriptRoot/build/picoscan_sdk_probe.exe"
if (!(Test-Path $exe)) { $exe = "$PSScriptRoot/build/Release/picoscan_sdk_probe.exe" }
if (!(Test-Path $exe)) { throw 'Build the probe with build.ps1 first.' }
if (Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "UDP port $Port is occupied. Stop the scanner listener before testing."
}
New-Item -ItemType Directory -Force "$PSScriptRoot/output" | Out-Null
$output = "$PSScriptRoot/output/$(Get-Date -Format yyyyMMdd-HHmmss-fff).csv"
& $exe $BindIP $SensorIP $Port $Seconds $output 2>&1 | Tee-Object -FilePath "$output.log"
$result = $LASTEXITCODE
Write-Output "CSV: $output"
if ($result -eq 3) { Write-Warning 'No UDP data received. Check scanner destination, Compact format and firewall.' }
if ($result -ne 0) { throw "Probe exit code: $result" }
python "$PSScriptRoot/analyze.py" $output | Tee-Object -FilePath "$output.summary.json"
if ($LASTEXITCODE -ne 0) { throw 'CSV analysis failed.' }
