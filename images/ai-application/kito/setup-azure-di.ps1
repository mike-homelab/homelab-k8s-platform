$ErrorActionPreference = "Stop"

# ===== CONFIGURATION =====
$RESOURCE_GROUP = "rg-ai-app-tiko"
$LOCATION = "eastus"
$VAULT_NAME = "kv-kito-prod"

# We check for the user's manual resource first, then fall back to the default name.
$DEFAULT_RESOURCE_NAME = "di-kito-prod"
$MANUAL_RESOURCE_NAME = "homelab-doc-inteligence"

$RESOURCE_NAME = ""

Write-Host "Checking for existing Azure Document Intelligence resources..."

$hasManual = $false
try {
    $null = az cognitiveservices account show --name $MANUAL_RESOURCE_NAME --resource-group $RESOURCE_GROUP -o json 2>$null
    if ($LASTEXITCODE -eq 0) { $hasManual = $true }
}
catch {
    # Ignored
}

$hasDefault = $false
if (-not $hasManual) {
    try {
        $null = az cognitiveservices account show --name $DEFAULT_RESOURCE_NAME --resource-group $RESOURCE_GROUP -o json 2>$null
        if ($LASTEXITCODE -eq 0) { $hasDefault = $true }
    }
    catch {
        # Ignored
    }
}

if ($hasManual) {
    Write-Host "Found existing manual resource: $MANUAL_RESOURCE_NAME"
    $RESOURCE_NAME = $MANUAL_RESOURCE_NAME
}
elseif ($hasDefault) {
    Write-Host "Found existing resource: $DEFAULT_RESOURCE_NAME"
    $RESOURCE_NAME = $DEFAULT_RESOURCE_NAME
}
else {
    Write-Host "No existing Document Intelligence resource found. Creating a new one: $DEFAULT_RESOURCE_NAME..."
    az cognitiveservices account create `
        --name $DEFAULT_RESOURCE_NAME `
        --resource-group $RESOURCE_GROUP `
        --kind FormRecognizer `
        --sku S0 `
        --location $LOCATION `
        --yes
    $RESOURCE_NAME = $DEFAULT_RESOURCE_NAME
}

# Retrieve endpoint and key
Write-Host "Retrieving connection details for $RESOURCE_NAME..."
$ENDPOINT = (az cognitiveservices account show --name $RESOURCE_NAME --resource-group $RESOURCE_GROUP --query "properties.endpoint" -o tsv).Trim()
$KEY = (az cognitiveservices account keys list --name $RESOURCE_NAME --resource-group $RESOURCE_GROUP --query "key1" -o tsv).Trim()

# Save to Key Vault
Write-Host "Updating Azure Key Vault secrets in '$VAULT_NAME'..."

$null = az keyvault secret set `
    --vault-name $VAULT_NAME `
    --name "azure-di-endpoint" `
    --value $ENDPOINT `
    --description "Azure Document Intelligence Endpoint for Kito Bot"

$null = az keyvault secret set `
    --vault-name $VAULT_NAME `
    --name "azure-di-key" `
    --value $KEY `
    --description "Azure Document Intelligence Key 1 for Kito Bot"

Write-Host "✅ Automation successfully complete!"
Write-Host "Endpoint stored: $ENDPOINT"
Write-Host "API Key stored: (masked)"
Write-Host "ExternalSecret kito-slack-secrets will sync these keys into your cluster."
