param([string]$WorkRoot = "$env:TEMP", [string]$ToolchainBin = '')
$ErrorActionPreference = 'Stop'
function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed: $LASTEXITCODE" }
}
$sdk = Join-Path $WorkRoot 'sick-perception-test'
$plog = Join-Path $WorkRoot 'picoscan-sdk-plog'
$zlib = Join-Path $WorkRoot 'picoscan-sdk-zlib'
$envRoot = Join-Path $WorkRoot 'picoscan-sdk-build-env'
$prefix = Join-Path $WorkRoot 'picoscan-sdk-install'
$revision = '3213a0098a82e8e440a4887087d79e972584f168'
if (!(Test-Path $sdk)) {
    Run git @('clone', 'https://github.com/SICKAG/sick_perception_sdk.git', $sdk)
    Run git @('-C', $sdk, 'checkout', '--detach', $revision)
}
if ((& git -C $sdk rev-parse HEAD).Trim() -ne $revision) { throw 'Unexpected SDK revision; use a new WorkRoot.' }
if (!(Test-Path $plog)) { Run git @('clone', '--depth', '1', '--branch', '1.1.10', 'https://github.com/SergiusTheBest/plog.git', $plog) }
if (!(Test-Path $zlib)) { Run git @('clone', '--depth', '1', '--branch', 'v1.3.1', 'https://github.com/madler/zlib.git', $zlib) }
if (!(Test-Path "$envRoot/Scripts/python.exe")) { Run python @('-m', 'venv', $envRoot) }
Run "$envRoot/Scripts/python.exe" @('-m', 'pip', 'install', 'cmake==4.4.4', 'ninja')
$cmake = "$envRoot/Scripts/cmake.exe"
$env:PATH = "$envRoot/Scripts;$env:PATH"
$generator = @('-G', 'Visual Studio 17 2022', '-A', 'x64')
$suffix = 'msvc'
if ($ToolchainBin) {
    $ToolchainBin = (Resolve-Path $ToolchainBin).Path
    $env:PATH = "$ToolchainBin;$env:PATH"
    # SDK PointCloudBuilder.hpp relies on an indirect <iterator> include.
    $generator = @('-G', 'Ninja', "-DCMAKE_C_COMPILER=$ToolchainBin/clang.exe", "-DCMAKE_CXX_COMPILER=$ToolchainBin/clang++.exe", '-DCMAKE_CXX_FLAGS=-include iterator')
    $suffix = 'clang'
}
$prefix = "$prefix-$suffix"
$plogBuild = Join-Path $WorkRoot "picoscan-plog-build-$suffix"
$sdkBuild = Join-Path $WorkRoot "picoscan-native-build-$suffix"
Run $cmake (@('-S', $plog, '-B', $plogBuild, "-DCMAKE_INSTALL_PREFIX=$prefix", '-DPLOG_BUILD_SAMPLES=OFF', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5') + $generator)
Run $cmake @('--install', $plogBuild, '--config', 'Release')
$zlibBuild = Join-Path $WorkRoot "picoscan-zlib-build-$suffix"
Run $cmake (@('-S', $zlib, '-B', $zlibBuild, "-DCMAKE_INSTALL_PREFIX=$prefix", '-DZLIB_BUILD_EXAMPLES=OFF', '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5') + $generator)
Run $cmake @('--build', $zlibBuild, '--config', 'Release', '--parallel', '4')
Run $cmake @('--install', $zlibBuild, '--config', 'Release')
Run $cmake (@('-S', $sdk, '-B', $sdkBuild, "-DCMAKE_PREFIX_PATH=$prefix", "-DCMAKE_INSTALL_PREFIX=$prefix", '-DBUILD_DRIVERS=OFF', '-DBUILD_SENSOR_CONFIGURATION=OFF', '-DBUILD_COMPACT_FORMAT=ON', '-DTRY_TO_USE_ZLIB=ON', '-DCMAKE_BUILD_TYPE=Release') + $generator)
Run $cmake @('--build', $sdkBuild, '--config', 'Release', '--parallel', '4')
Run $cmake @('--install', $sdkBuild, '--config', 'Release')
Run $cmake (@('-S', $PSScriptRoot, '-B', "$PSScriptRoot/build", "-DCMAKE_PREFIX_PATH=$prefix", '-DCMAKE_BUILD_TYPE=Release') + $generator)
Run $cmake @('--build', "$PSScriptRoot/build", '--config', 'Release', '--parallel', '4')
# Keep portable compiler runtime DLLs beside the executable for later runs.
if ($ToolchainBin) {
    foreach ($name in @('libc++.dll', 'libunwind.dll', 'libwinpthread-1.dll')) {
        $dll = Join-Path $ToolchainBin $name
        if (Test-Path $dll) { Copy-Item -LiteralPath $dll -Destination "$PSScriptRoot/build" }
    }
}
