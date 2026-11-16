param(
    [string]$SdkZip,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$vendor = Join-Path $root 'vendor'
$sdk = Join-Path $vendor 'CubismSdkForWeb-5-r.3'
$demo = Join-Path $sdk 'Samples/TypeScript/Demo'
if ($SdkZip) {
    if (Test-Path -LiteralPath $sdk) {
        throw 'The SDK directory already exists. Run without -SdkZip to reuse it.'
    }
    New-Item -ItemType Directory -Path $vendor -Force | Out-Null
    Expand-Archive -LiteralPath $SdkZip -DestinationPath $vendor
}
if (-not (Test-Path -LiteralPath (Join-Path $sdk 'Core/live2dcubismcore.js'))) {
    throw 'Place the official Cubism SDK for Web 5-r.3 in vendor/ or supply -SdkZip.'
}
$overlay = Join-Path $root 'live2d-overlay'
$overlayFolders = Get-ChildItem -LiteralPath $overlay -Directory
foreach ($folder in $overlayFolders) {
    foreach ($file in Get-ChildItem -LiteralPath $folder.FullName -File -Recurse) {
        $relative = $file.FullName.Substring($overlay.Length + 1)
        $destination = Join-Path $sdk $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}
$core = Join-Path $demo 'public/Core'
New-Item -ItemType Directory -Path $core -Force | Out-Null
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $sdk 'Core') -File) {
    Copy-Item -LiteralPath $file.FullName -Destination $core -Force
}
Push-Location $demo
try {
    if (-not $SkipInstall) {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
    }
    & node.exe './node_modules/typescript/bin/tsc' --noEmit --lib 'ES2020,DOM'
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript check failed.' }
    & node.exe './node_modules/vite/bin/vite.js' build --outDir dist-desktop
    if ($LASTEXITCODE -ne 0) { throw 'Desktop renderer build failed.' }
} finally {
    Pop-Location
}
