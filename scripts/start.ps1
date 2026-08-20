$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

uv run vl stop
if ($LASTEXITCODE -ne 0) {
	exit $LASTEXITCODE
}

uv run vl start @args
