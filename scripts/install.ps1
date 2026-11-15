[CmdletBinding()]
param(
    [switch]$UseUv,
    [switch]$WithDev,
    [string]$VenvPath,
    [string]$PythonExecutable
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $VenvPath) { $VenvPath = Join-Path $projectRoot '.writune-venv' }
$environmentPath = [IO.Path]::GetFullPath($VenvPath)
$ownerPath = Join-Path $environmentPath '.writune-owner'
$venvPython = Join-Path $environmentPath 'Scripts\python.exe'
$packageSpec = $projectRoot
if ($WithDev) { $packageSpec += '[dev]' }
if (Test-Path -LiteralPath $environmentPath) {
    if (-not (Test-Path -LiteralPath $ownerPath) -or
        [IO.File]::ReadAllText($ownerPath).Trim() -ne $projectRoot) {
        throw "Refusing to modify an existing unowned environment: $environmentPath. Choose a new -VenvPath."
    }
    if (-not (Test-Path -LiteralPath $venvPython)) { throw 'The Writune environment is incomplete; choose a new -VenvPath.' }
}
$isolatedEnvironment = @{
    PYTHONNOUSERSITE = '1'
    PIP_REQUIRE_VIRTUALENV = 'true'
    UV_PYTHON_INSTALL_DIR = (Join-Path $projectRoot '.writune-runtime\python')
    UV_PYTHON_BIN_DIR = (Join-Path $projectRoot '.writune-runtime\bin')
    UV_CACHE_DIR = (Join-Path ([IO.Path]::GetTempPath()) 'writune-uv-cache')
    UV_PYTHON_INSTALL_REGISTRY = 'false'
    UV_PYTHON_NO_REGISTRY = 'true'
    UV_PYTHON_INSTALL_BIN = 'false'
    UV_NO_MODIFY_PATH = 'true'
}
$priorEnvironment = @{}
try {
    foreach ($name in $isolatedEnvironment.Keys) {
        $priorEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $isolatedEnvironment[$name], 'Process')
    }
    if ($UseUv) {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv was not found. Supply a standalone uv executable on this process PATH or use an existing Python 3.11+.' }
        if (-not (Test-Path -LiteralPath $venvPython)) {
            & uv --no-config venv --managed-python --python 3.12 $environmentPath
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the private uv-managed environment.' }
            [IO.File]::WriteAllText($ownerPath, $projectRoot)
        }
        & uv --no-config pip install --python $venvPython --editable $packageSpec
        if ($LASTEXITCODE -ne 0) { throw 'Installation into the Writune environment failed.' }
    }
    else {
        if (-not (Test-Path -LiteralPath $venvPython)) {
            $pythonCommand = $PythonExecutable
            $pythonArgs = @()
            if (-not $pythonCommand) {
                if (Get-Command py -ErrorAction SilentlyContinue) { $pythonCommand = 'py'; $pythonArgs = @('-3') }
                else { $pythonCommand = 'python' }
            }
            & $pythonCommand @pythonArgs -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required. Use -PythonExecutable or -UseUv.' }
            & $pythonCommand @pythonArgs -I -m venv $environmentPath
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the private virtual environment.' }
            [IO.File]::WriteAllText($ownerPath, $projectRoot)
        }
        & $venvPython -I -c 'import sys; assert sys.prefix != sys.base_prefix, "An isolated venv is required"'
        if ($LASTEXITCODE -ne 0) { throw 'The selected interpreter is not isolated.' }
        & $venvPython -I -m pip --isolated install --editable $packageSpec
        if ($LASTEXITCODE -ne 0) { throw 'Installation into the Writune environment failed.' }
    }
}
finally {
    foreach ($name in $priorEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $priorEnvironment[$name], 'Process')
    }
}
Write-Host "Installed in: $environmentPath"
Write-Host "Initialize: & '$venvPython' -m writune init"
Write-Host 'Existing Python installations, environments, PATH and registry are unchanged.'
