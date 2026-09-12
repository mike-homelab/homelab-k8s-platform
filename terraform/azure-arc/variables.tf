variable "resource_group_name" {
  type        = string
  description = "The name of the Azure resource group"
  default     = "mike-homelab-rg"
}

variable "location" {
  type        = string
  description = "Azure region for the resources"
  default     = "eastus"
}

variable "workspace_name" {
  type        = string
  description = "Name of the Log Analytics Workspace"
  default     = "law-homelab-eastus"
}

variable "daily_quota_gb" {
  type        = number
  description = "Daily ingestion quota in GB for Log Analytics"
  default     = 0.4
}

variable "tags" {
  type        = map(string)
  description = "Resource tags applied to all provisioned resources"
  default = {
    Environment = "Homelab"
    Project     = "Homelab-K8s-Platform"
    ManagedBy   = "Terraform"
    CostCenter  = "FreeTier-Capped"
  }
}
