# Lecture seule : ne redemarre pas le ML, ne lance ni calibration ni attaque.
$ErrorActionPreference = 'Stop'
$racine = Split-Path -Parent $PSScriptRoot
$stack = Join-Path $racine 'Backend/security/dos-stack.cmd'
$diagnostic = Join-Path $racine 'Backend/security/diagnose_calibration.py'
$rapport = Join-Path $racine 'Backend/security/runtime/dos-calibration.json'
if (-not (Test-Path -LiteralPath $rapport)) { throw 'Rapport de calibration introuvable sur ce PC.' }
& $stack run --rm --no-deps -T `
    -v "${diagnostic}:/diagnose_calibration.py:ro" `
    -v "${rapport}:/reports/dos-calibration.json:ro" `
    ml-api python /diagnose_calibration.py
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic incomplet. Conserver le rapport et les messages precedents.' }
