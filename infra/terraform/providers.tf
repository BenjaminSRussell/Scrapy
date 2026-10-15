provider "aws" {
  region = var.aws_region

  # No static credentials and no shared credentials file. An operator who
  # applies this outside CI uses their own environment or role.
  default_tags {
    tags = local.common_tags
  }
}
