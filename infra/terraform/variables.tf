variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "Region for the stub. Change the availability zones to match."
}

variable "name_prefix" {
  type        = string
  default     = "scraping-pipeline"
  description = "Lowercase prefix for stub resource names. Not a Helm release name."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,18}$", var.name_prefix))
    error_message = "name_prefix must be 1-19 lowercase letters, digits, or hyphens so ElastiCache ids stay within 40 characters."
  }
}

variable "vpc_cidr" {
  type        = string
  default     = "10.20.0.0/16"
  description = "CIDR for the stub VPC. Private subnets only; no internet gateway."
}

variable "availability_zones" {
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
  description = "Zones for the private subnets. Must match aws_region."

  validation {
    condition     = length(var.availability_zones) >= 2 && alltrue([for az in var.availability_zones : startswith(az, var.aws_region)])
    error_message = "Provide at least two availability zones in aws_region."
  }
}

variable "private_subnet_cidrs" {
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
  description = "Private subnet CIDRs, one per availability zone. Must sit inside vpc_cidr."

  validation {
    condition     = length(var.private_subnet_cidrs) == length(var.availability_zones)
    error_message = "private_subnet_cidrs and availability_zones must be the same length."
  }
}

variable "lake_bucket_name" {
  type        = string
  default     = "example-scraping-delta-lake"
  description = "Bucket name for the Delta lake object store. Replace before any real apply. The same string is the MinIO bucket name; this module does not create MinIO."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.lake_bucket_name))
    error_message = "lake_bucket_name must be a DNS-style S3 bucket name."
  }
}

variable "object_store_backend" {
  type        = string
  default     = "s3"
  description = "s3 declares the AWS bucket. minio records an endpoint only and still does not provision MinIO or accept keys."

  validation {
    condition     = contains(["s3", "minio"], var.object_store_backend)
    error_message = "object_store_backend must be s3 or minio."
  }
}

variable "minio_endpoint" {
  type        = string
  default     = ""
  description = "Optional MinIO (S3-compatible) endpoint, host and port only. Empty means unset. Do not put keys or a userinfo in the URL."

  validation {
    condition     = var.minio_endpoint == "" || can(regex("^https?://[A-Za-z0-9.-]+(:[0-9]+)?$", var.minio_endpoint))
    error_message = "minio_endpoint must be empty or an http(s) host[:port] with no credentials."
  }
}

variable "redis_node_type" {
  type        = string
  default     = "cache.t4g.small"
  description = "ElastiCache node size. Placeholder only; not a measured capacity. The chart's in-cluster Redis limit is 2Gi and is not copied here."
}

variable "redis_auth_token" {
  type        = string
  default     = null
  sensitive   = true
  description = "Redis AUTH token (REDIS_PASSWORD). Null in git. Set TF_VAR_redis_auth_token only for a deliberate apply. 16-128 characters when set."

  validation {
    condition     = var.redis_auth_token == null || can(regex("^[^\\s]{16,128}$", var.redis_auth_token))
    error_message = "redis_auth_token must be null or 16-128 non-space characters. Do not commit it."
  }
}

variable "allow_apply" {
  type        = bool
  default     = false
  description = "Safety latch. Leave false. Plan refuses until a human sets this true outside CI. CI only runs fmt and validate."
}
