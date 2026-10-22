# PC hote : reference existante, stabilisation 90s puis validation normale 120s.
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
# Ensure our own profile polling cannot contaminate the business metrics.
$instrumentation = & $stack exec -T payment-api python -c "import inspect,main; print('excluded' if '/lab-profile' in inspect.getsource(main.observe_requests) else 'included')"
if ($LASTEXITCODE -ne 0) { throw 'Impossible de verifier les metriques de payment-api.' }
if (($instrumentation -join "`n").Trim() -ne 'excluded') {
    Write-Host 'Correction des metriques : reconstruction de payment-api uniquement. Le ML est conserve.' -ForegroundColor Cyan
    & $stack up -d --build --no-deps payment-api
    if ($LASTEXITCODE -ne 0) { throw 'Correction de payment-api impossible.' }
    $paiementDisponible = $false
    for ($verification = 0; $verification -lt 30; $verification++) {
        try {
            $null = & $stack exec -T payment-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()" 2>$null
            if ($LASTEXITCODE -eq 0) { $paiementDisponible = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $paiementDisponible) { throw 'payment-api non disponible : consulter ses logs.' }
    $instrumentation = & $stack exec -T payment-api python -c "import inspect,main; print('excluded' if '/lab-profile' in inspect.getsource(main.observe_requests) else 'included')"
    if ($LASTEXITCODE -ne 0 -or ($instrumentation -join "`n").Trim() -ne 'excluded') {
        throw 'Correction des metriques absente de cette copie du depot : recuperer la nouvelle version.'
    }
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
Write-Host 'Stabilisation 90s puis validation 120s : aucune attaque et aucun autre generateur de trafic.' -ForegroundColor Yellow
& $stack run --rm --no-deps -T `
    -v "${runtime}:/reports" `
    -v "${outil}:/app/repair_calibration.py:ro" `
    remediator python -u /app/repair_calibration.py
if ($LASTEXITCODE -ne 0) { throw 'Verification non concluante : consulter le rapport et les logs, ne pas forcer ready.' }
Write-Host 'Calibration verifiee et reference ML conservee sur volume Docker.' -ForegroundColor Green
Write-Host 'Vous pouvez lancer cyber/jury-hote.ps1 puis cyber/jury-pc2.ps1 au signal.'
