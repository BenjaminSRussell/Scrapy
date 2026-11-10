locals {
  common_tags = {
    Project   = "scraping-pipeline"
    ManagedBy = "terraform"
    Component = "cloud-stub"
  }

  # MinIO is not a resource. The flag only records the operator's intent.
  minio_selected = var.object_store_backend == "minio"
}
