output "vpc_id" {
  description = "Stub VPC. Not wired to the Helm chart."
  value       = aws_vpc.pipeline.id
}

output "private_subnet_ids" {
  description = "Private subnets. No NAT and no internet gateway are created."
  value       = aws_subnet.private[*].id
}

output "lake_bucket" {
  description = "Object-store name. S3 resources exist in this module. MinIO is not created and no keys are output."
  value = {
    backend        = var.object_store_backend
    bucket         = aws_s3_bucket.lake.bucket
    minio_selected = local.minio_selected
    minio_endpoint = var.minio_endpoint == "" ? null : var.minio_endpoint
    minio_managed  = false
  }
}

output "redis_env" {
  description = "REDIS_HOST and REDIS_PORT. REDIS_DB is 0. REDIS_PASSWORD is var.redis_auth_token and is not an output. Do not set REDIS_URL."
  value = {
    REDIS_HOST = aws_elasticache_replication_group.redis.primary_endpoint_address
    REDIS_PORT = aws_elasticache_replication_group.redis.port
    REDIS_DB   = 0
  }
}

output "apply_allowed" {
  description = "False unless a human set allow_apply. CI does not."
  value       = var.allow_apply
}
