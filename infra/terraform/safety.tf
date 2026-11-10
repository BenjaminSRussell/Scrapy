# Checked at plan time, not by `terraform validate`, so CI can validate this
# stub with allow_apply left false and with no credentials.
resource "terraform_data" "apply_latch" {
  input = {
    allow_apply = var.allow_apply
  }

  lifecycle {
    precondition {
      condition     = var.allow_apply && var.redis_auth_token != null
      error_message = "Refusing to plan this stub. Set allow_apply=true and TF_VAR_redis_auth_token only for a deliberate apply outside CI. Do not commit the token. CI runs fmt and validate and does not apply."
    }
  }
}
