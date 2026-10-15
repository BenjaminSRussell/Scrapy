terraform {
  required_version = ">= 1.5.0"

  # Local state only. Do not point this at a remote backend in-repo: that
  # needs a real bucket and credentials, and CI must not apply or migrate state.
  backend "local" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
  }
}
