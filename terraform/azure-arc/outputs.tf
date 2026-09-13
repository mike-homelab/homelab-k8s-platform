output "workspace_id" {
  description = "The Resource ID of the Log Analytics workspace"
  value       = azurerm_log_analytics_workspace.law.id
}

output "workspace_customer_id" {
  description = "The Customer / Workspace ID for agent connection"
  value       = azurerm_log_analytics_workspace.law.workspace_id
}

output "workspace_primary_key" {
  description = "Primary shared key for the Log Analytics workspace"
  value       = azurerm_log_analytics_workspace.law.primary_shared_key
  sensitive   = true
}

output "key_vault_id" {
  description = "The Resource ID of the Key Vault"
  value       = azurerm_key_vault.kv.id
}

output "key_vault_uri" {
  description = "The URI of the Key Vault for External Secrets Operator"
  value       = azurerm_key_vault.kv.vault_uri
}

output "key_vault_name" {
  description = "The name of the Key Vault"
  value       = azurerm_key_vault.kv.name
}
