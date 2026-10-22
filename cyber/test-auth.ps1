param(
    [string]$AuthApiUrl = "http://192.168.137.1:8001"
)

# Utiliser un compte fictif dedie au laboratoire.
# Les identifiants et le JWT restent en memoire pendant l'execution.
$ErrorActionPreference = "Stop"
$compteTest = $null
$connexion = $null
$sessionTest = $null
$entetesTest = $null

try {
    $compteTest = Get-Credential -Message "Compte fictif : adresse e-mail et mot de passe"
    if ($null -eq $compteTest) {
        throw "Saisie des identifiants annulee."
    }

    $connexion = @{
        email    = $compteTest.UserName
        password = $compteTest.GetNetworkCredential().Password
    } | ConvertTo-Json

    $sessionTest = Invoke-RestMethod `
        -Method Post `
        -Uri "$($AuthApiUrl.TrimEnd('/'))/auth/login" `
        -ContentType "application/json" `
        -Body $connexion `
        -TimeoutSec 15

    if ([string]::IsNullOrWhiteSpace($sessionTest.access_token)) {
        throw "Le service n'a pas retourne de jeton d'acces."
    }

    $entetesTest = @{
        Authorization = "Bearer $($sessionTest.access_token)"
    }

    Invoke-RestMethod `
        -Uri "$($AuthApiUrl.TrimEnd('/'))/auth/me" `
        -Headers $entetesTest `
        -TimeoutSec 15 |
        Select-Object id, email, role
}
finally {
    # Retirer les references aux identifiants et au jeton apres le test.
    $connexion = $null
    $sessionTest = $null
    $entetesTest = $null
    $compteTest = $null
}
