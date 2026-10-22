# PC hote : reutilise la reference existante, puis 120s de validation normale.
# Ne lance aucune attaque, ne modifie pas l'authentification ni la defense.
param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$racine = Split-Path -Parent $PSScriptRoot
$stack = Join-Path $racine 'Backend/security/dos-stack.cmd'
$runtime = Join-Path $racine 'Backend/security/runtime'
$outil = Join-Path $racine 'Backend/security/repair_calibration.py'
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'dos-calibration.json'))) {
    throw 'Rapport historique absent. Ne pas inventer de reference : recuperer le rapport du PC hote.'
}
# Echouer AVANT reconstruction si la session est trop ancienne.
& $stack exec -T remediator python -c "from lab_session import load_session; load_session(minimum=600); print('Session valide pour la verification courte')"
if ($LASTEXITCODE -ne 0) {
    throw 'Renouveler uniquement la session avec cyber/prepare-lab.ps1 (sans -ForCalibration), puis reessayer.'
}
if (-not $SkipBuild) {
    Write-Host 'Mise a jour du ML. La reference sera recuperee dans Prometheus, sans collecte de 20 minutes.' -ForegroundColor Cyan
    & $stack up -d --build --no-deps ml-api
    if ($LASTEXITCODE -ne 0) { throw 'Mise a jour du ML impossible.' }
} else {
    Write-Host 'Reprise sur le conteneur ML existant, sans reconstruction ni redemarrage.' -ForegroundColor Cyan
}
$disponible = $false
for ($tentative = 0; $tentative -lt 30; $tentative++) {
    try {
        # Internal readiness is independent of Windows localhost forwarding.
        $null = & $stack exec -T ml-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=2).read()" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $disponible = $true
            break
        }
    } catch { Start-Sleep -Seconds 1 }
    if (-not $disponible) { Start-Sleep -Seconds 1 }
}
if (-not $disponible) { throw 'ML indisponible : consulter les logs ml-api.' }
# Fail clearly if -SkipBuild was used on the old image.
& $stack exec -T ml-api python -c "from detector import MODEL_VERSION; print('Detecteur charge : ' + MODEL_VERSION)"
if ($LASTEXITCODE -ne 0) { throw 'Ancienne image ML : relancer le script sans -SkipBuild.' }
Write-Host 'Pendant les 120s suivantes : aucune attaque et aucun autre generateur de trafic.' -ForegroundColor Yellow
& $stack run --rm --no-deps -T `
    -v "${runtime}:/reports" `
    -v "${outil}:/app/repair_calibration.py:ro" `
    remediator python -u /app/repair_calibration.py
if ($LASTEXITCODE -ne 0) { throw 'Verification non concluante : consulter le rapport et les logs, ne pas forcer ready.' }
Write-Host 'Calibration verifiee et reference ML conservee sur volume Docker.' -ForegroundColor Green
Write-Host 'Vous pouvez lancer cyber/jury-hote.ps1 puis cyber/jury-pc2.ps1 au signal.'
