# PC 2 uniquement : trafic borne, sans preparation Docker.
$ErrorActionPreference = 'Stop'
$racine = Split-Path -Parent $PSScriptRoot
$scriptTrafic = Join-Path $racine 'Backend/security/lab_traffic.py'
$dossier = Join-Path $PSScriptRoot 'results'
$null = Get-Command python -ErrorAction Stop

$sante = Invoke-RestMethod 'http://192.168.137.1:8080/health' -TimeoutSec 5
if ($sante.status -ne 'healthy') {
    throw 'Le WAF ne confirme pas un service sain. Corriger avant le test.'
}

Write-Host 'PC 2 pret. Attendre que le PC hote affiche deux paiements reussis.' -ForegroundColor Cyan
$null = Read-Host 'Au signal du collegue, appuyer sur Entree'
$null = New-Item -ItemType Directory -Path $dossier -Force
$rapport = Join-Path $dossier ("dos-traffic-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

Write-Host 'Compte fictif user. Charge : 4 departs/s, 4 requetes simultanees max, 120s. Arret : Ctrl+C.'
& python $scriptTrafic --url 'http://192.168.137.1:8080' --rps 4 --seconds 120 --output $rapport
if ($LASTEXITCODE -ne 0) {
    throw 'Le generateur a echoue. Ne pas annoncer un essai reussi.'
}
Write-Host "Rapport : $rapport" -ForegroundColor Cyan
