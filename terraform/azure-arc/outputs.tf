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
