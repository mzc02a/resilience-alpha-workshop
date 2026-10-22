param([switch]$ForCalibration)

# PC hote : utiliser une session obtenue dans le navigateur apres visage + mot de passe + 30s.
$ErrorActionPreference = 'Stop'
$racine = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $racine 'Backend/security/runtime'
$secret = Read-Host 'Coller le JWT de session du compte fictif (saisie masquee)' -AsSecureString
$pointeur = [IntPtr]::Zero
$jeton = $null
$entetes = $null
$contenu = $null
try {
    $pointeur = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $jeton = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointeur).Trim()
    try {
        $partie = $jeton.Split('.')[1].Replace('-', '+').Replace('_', '/')
        $partie = $partie.PadRight($partie.Length + ((4 - $partie.Length % 4) % 4), '=')
        $claims = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($partie)) | ConvertFrom-Json
        $maintenant = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $minimum = 600
        if ($ForCalibration) { $minimum = 1260 }
        if ($claims.auth_flow -ne 'identifier_face_password' -or
            $claims.face_method -ne 'image_comparison' -or
            $null -eq $claims.exp -or $null -eq $claims.face_verified_until -or
            [double]$claims.nbf -gt $maintenant -or
            [double]$claims.exp -lt $maintenant + $minimum -or
            [double]$claims.face_verified_until -lt $maintenant + $minimum) {
            throw 'Session non utilisable.'
        }
    } catch {
        throw 'Session non active ou trop courte : refaire la connexion complete puis attendre les 30 secondes.'
    }

    # La verification locale sert seulement au calendrier ; les API verifient la signature et les droits.
    $entetes = @{ Authorization = "Bearer $jeton" }
    try {
        $null = Invoke-RestMethod 'http://localhost:8001/auth/me' -Headers $entetes -TimeoutSec 5
        Start-Sleep -Seconds 1
        $compte = Invoke-RestMethod 'http://localhost:8000/accounts/me' -Headers $entetes -TimeoutSec 5
        Start-Sleep -Seconds 1
        $beneficiaires = Invoke-RestMethod 'http://localhost:8000/beneficiaries' -Headers $entetes -TimeoutSec 5
    } catch {
        throw 'Les API refusent la session ou sont indisponibles. Verifier la connexion et la protection dans Grafana.'
    }
    $iban = (Read-Host 'IBAN fictif du destinataire existant (autre compte)').Replace(' ', '').ToUpperInvariant()
    if ($iban -eq $compte.iban -or -not @($beneficiaires | Where-Object { $_.iban -eq $iban }).Count) {
        throw 'Choisir un beneficiaire existant different du compte connecte.'
    }
    if ([decimal]$compte.balance -lt 1) { throw 'Le compte de sonde doit disposer de fonds fictifs.' }

    $null = New-Item -ItemType Directory -Path $runtime -Force
    $destination = Join-Path $runtime 'probe-session.json'
    $contenu = @{ access_token = $jeton; recipient_iban = $iban } | ConvertTo-Json
    $temporaire = Join-Path $runtime 'probe-session.tmp'
    [IO.File]::WriteAllText($temporaire, $contenu, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporaire -Destination $destination -Force
    Write-Host 'Session de laboratoire preparee. Aucun paiement cree et aucun secret affiche.' -ForegroundColor Green
    Write-Host 'La calibration peut utiliser cette session avec -ForCalibration ; sinon lancer jury-hote.ps1.'
} finally {
    if ($pointeur -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointeur) }
    $jeton = $null
    $entetes = $null
    $contenu = $null
    $secret = $null
}
