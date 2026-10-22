# PC hote uniquement. Ne demarre/recalibre aucun service et n'active pas la defense.
$ErrorActionPreference = 'Stop'
$racine = Split-Path -Parent $PSScriptRoot
$stack = Join-Path $racine 'Backend/security/dos-stack.cmd'
$runtime = Join-Path $racine 'Backend/security/runtime'
$null = Get-Command docker -ErrorAction Stop

# Lire seulement la duree restante via le helper ; ne jamais afficher le JWT.
$null = & $stack exec -T remediator python -c "from lab_session import load_session; load_session(minimum=360); print('Session de sonde prete')"
if ($LASTEXITCODE -ne 0) {
    throw 'Preparer une session faciale recente avec cyber/prepare-lab.ps1 avant le passage.'
}

$sante = Invoke-RestMethod 'http://localhost:8002/health' -TimeoutSec 5
$ml = Invoke-RestMethod 'http://localhost:8002/status' -TimeoutSec 5
$controleur = Invoke-RestMethod 'http://localhost:8003/status' -TimeoutSec 5
$calibration = Get-Content (Join-Path $runtime 'dos-calibration.json') -Raw | ConvertFrom-Json
$maintenant = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

if ($sante.model_ready -ne $true -or $calibration.state -ne 'ready') {
    throw 'Calibration absente ou modele non pret : terminer la preparation hors jury.'
}
if ($ml.state -ne 'normal' -or $null -eq $ml.timestamp -or
    $maintenant - [double]$ml.timestamp -gt 60 -or [double]$ml.timestamp -gt $maintenant) {
    throw 'Le ML ne confirme pas un etat normal recent. Attendre ou diagnostiquer avant le passage.'
}
if ($controleur.state -ne 'monitoring') {
    throw "Controleur non pret : $($controleur.state)."
}
if ($controleur.last_action.activated_at -and
    $maintenant - [double]$controleur.last_action.activated_at -lt 180) {
    throw 'Attendre 180 secondes depuis la derniere activation avant un nouvel essai.'
}

# Confirmer le profil reellement charge dans payment-api, sans lire les secrets.
$profilBrut = & $stack exec -T payment-api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/lab-profile').read().decode())"
if ($LASTEXITCODE -ne 0) { throw 'Impossible de verifier le profil du laboratoire.' }
$profil = ($profilBrut -join "`n") | ConvertFrom-Json
if ($profil.enabled -ne $true) { throw 'Le profil a capacite reduite doit etre active avant le passage.' }

# Verifier aussi la protection actuelle dans la passerelle.
$protectionBrute = & $stack exec -T gateway python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/internal/metrics').read().decode())"
if ($LASTEXITCODE -ne 0) { throw 'Impossible de verifier la protection.' }
if (($protectionBrute -join "`n") -notmatch '(?m)^gateway_protection_active 0(?:\.0)?\s*$') {
    throw 'La protection doit etre inactive au depart.'
}

Write-Host 'PRET : profil de laboratoire actif, modele normal, protection inactive.' -ForegroundColor Green
Write-Host 'Ouvrir Grafana. La sonde va creer des paiements fictifs de 1 EUR pendant 240s.'
$null = Read-Host 'Pour commencer la demonstration, appuyer sur Entree'
$rapport = "jury-business-probe-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss')
$reussites = 0
$signalEnvoye = $false

& $stack run --rm --no-deps -v "${runtime}:/reports" remediator python -u business_probe.py --seconds 240 --output "/reports/$rapport" |
    ForEach-Object {
        $ligne = [string]$_
        if ($ligne.TrimStart().StartsWith('{')) {
            try { $sonde = $ligne | ConvertFrom-Json } catch { Write-Host $ligne; return }
            if ($null -eq $sonde.timestamp) { Write-Host $ligne; return }
            $heure = [DateTimeOffset]::FromUnixTimeSeconds([long]$sonde.timestamp).ToLocalTime().ToString('HH:mm:ss')
            if ($sonde.success -eq $true) {
                $reussites++
                Write-Host "$heure PAIEMENT REUSSI ET RELU - $($sonde.payment_id)" -ForegroundColor Green
                if ($reussites -ge 2 -and -not $signalEnvoye) {
                    Write-Host 'SIGNAL : le PC 2 peut lancer son trafic maintenant.' -ForegroundColor Cyan
                    $signalEnvoye = $true
                }
            } else {
                Write-Host "$heure ECHEC METIER - POST=$($sonde.post_status) lecture=$($sonde.read_status) erreur=$($sonde.error_type)" -ForegroundColor Red
            }
        } else { Write-Host $ligne }
    }
if ($LASTEXITCODE -ne 0) { throw 'La sonde a echoue : examiner les messages ci-dessus.' }
Write-Host "Rapport : $(Join-Path $runtime $rapport)" -ForegroundColor Cyan
Write-Host 'Comparer avec les horaires du trafic avant de conclure a une reprise sous charge.'
