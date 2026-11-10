param(
    [string]$PostgresImage = 'postgres:16'
)

# Disposable PostgreSQL regression check. No shared container, volume or DB is used.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$pythonExe = Join-Path $repo '.venv/Scripts/python.exe'
$containerName = 'scms-t009-check-' + [guid]::NewGuid().ToString('N')
$testPassword = [guid]::NewGuid().ToString('N')
$databaseFile = [IO.Path]::GetTempFileName()
$savedEnvironment = @{}
foreach ($variableName in @('TEST_REGISTRY_DATABASE_URL', 'REGISTRY_DATABASE_URL', 'REGISTRY_DATABASE_PATH', 'PYTHONPATH')) {
    $savedEnvironment[$variableName] = [Environment]::GetEnvironmentVariable($variableName, 'Process')
}
$testExit = 1
try {
    $containerId = & docker run --detach --rm --name $containerName --publish '127.0.0.1::5432' --env "POSTGRES_PASSWORD=$testPassword" --env 'POSTGRES_DB=scms_test' $PostgresImage
    if ($LASTEXITCODE -ne 0) { throw 'Could not start disposable PostgreSQL' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker exec $containerName pg_isready -h 127.0.0.1 -U postgres -d scms_test *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'PostgreSQL was not ready within 30 seconds' }
    $binding = & docker port $containerName '5432/tcp'
    if ($LASTEXITCODE -ne 0) { throw 'Could not find disposable PostgreSQL port' }
    $port = ($binding.Trim() -split ':')[-1]
    $env:TEST_REGISTRY_DATABASE_URL = "postgresql://postgres:${testPassword}@127.0.0.1:${port}/scms_test"
    $env:REGISTRY_DATABASE_URL = ''
    $env:REGISTRY_DATABASE_PATH = $databaseFile
    $env:PYTHONPATH = Join-Path $repo 'service-registry'
    Push-Location $repo
    try {
        & $pythonExe -m pytest service-registry/tests -q
        $testExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    & docker rm --force $containerName *> $null
    foreach ($variableName in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($variableName, $savedEnvironment[$variableName], 'Process')
    }
    # Delete only exact files created for this check, never a computed directory tree.
    foreach ($file in @($databaseFile, "$databaseFile-wal", "$databaseFile-shm")) {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
    }
}
exit $testExit
