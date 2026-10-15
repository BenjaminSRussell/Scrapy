terraform {
  required_version = ">= 1.5.0"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.13.0, < 3.0.0"
    }
  }
}

# Existing cluster only. Empty kubeconfig uses KUBECONFIG or ~/.kube/config.
# This root module does not create a VPC, a Kubernetes cluster, or a database.
provider "helm" {
  kubernetes {
    config_path    = var.kubeconfig_path == "" ? null : var.kubeconfig_path
    config_context = var.kubeconfig_context == "" ? null : var.kubeconfig_context
  }
}
