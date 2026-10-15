output "release_name" {
  description = "Helm release name."
  value       = helm_release.scraping_pipeline.name
}

output "namespace" {
  description = "Namespace the release is installed into."
  value       = helm_release.scraping_pipeline.namespace
}

output "profile" {
  description = "Values profile. dev means values.yaml only."
  value       = var.profile
}

output "chart_path" {
  description = "Absolute path of the wrapped Helm chart."
  value       = local.chart_path
}
