param(
    [string]$AuthApiUrl = "http://192.168.137.1:8001",
    [string]$PaymentApiUrl = "http://192.168.137.1:8000"
)

# Chaque execution cree un paiement fictif de 1 EUR en base.
# Aucun nouvel essai automatique : une erreur reseau peut survenir apres creation.
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

    $paiementTest = Invoke-RestMethod `
        -Method Post `
        -Uri "$($PaymentApiUrl.TrimEnd('/'))/payments" `
        -Headers $entetesTest `
        -ContentType "application/json" `
        -Body '{"amount":"1.00","currency":"EUR"}' `
        -TimeoutSec 15

    if ($paiementTest.status -ne "SUCCESS" -or
        [string]::IsNullOrWhiteSpace($paiementTest.id) -or
        [decimal]$paiementTest.amount -ne 1 -or
        $paiementTest.currency -ne "EUR") {
        throw "La reponse ne confirme pas un paiement reussi de 1 EUR."
    }

    # Relire la transaction pour verifier sa consultation apres creation.
    $paiementRelu = Invoke-RestMethod `
        -Uri "$($PaymentApiUrl.TrimEnd('/'))/payments/$($paiementTest.id)" `
        -Headers $entetesTest `
        -TimeoutSec 15

    if ($paiementRelu.id -ne $paiementTest.id -or
        $paiementRelu.status -ne "SUCCESS" -or
        [decimal]$paiementRelu.amount -ne 1 -or
        $paiementRelu.currency -ne "EUR") {
        throw "La relecture du paiement ne correspond pas au resultat attendu."
    }

    $paiementRelu | Select-Object id, amount, currency, status, created_at
}
finally {
    # Retirer les references aux identifiants et au jeton apres le test.
    $connexion = $null
    $sessionTest = $null
    $entetesTest = $null
    $compteTest = $null
}
