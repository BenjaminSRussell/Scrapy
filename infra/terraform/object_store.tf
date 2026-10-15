# S3 bucket for a future cloud Delta lake. The running pipeline still stores
# Delta on a filesystem (DELTA_LAKE_PATH / the Helm PVC), not in this bucket.
#
# MinIO: same lake_bucket_name, endpoint in var.minio_endpoint. There is no
# MinIO provider here, so apply does not start MinIO and there is nowhere to
# put an access key. object_store_backend = "minio" does not skip the AWS
# bucket resource; do not apply this module if you only wanted MinIO.

resource "aws_s3_bucket" "lake" {
  bucket = var.lake_bucket_name

  tags = merge(local.common_tags, {
    Name = var.lake_bucket_name
  })
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket = aws_s3_bucket.lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
