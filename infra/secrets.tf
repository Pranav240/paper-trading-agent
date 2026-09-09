# SSM Parameter Store, not Secrets Manager.
#
# Secrets Manager is the better-known answer and has rotation built in, but
# it bills $0.40 per secret per month. Four secrets is ~$19/year to store
# four short strings that never rotate, on a project whose entire OpenAI
# budget was $5. SSM SecureString parameters are free at the Standard tier,
# encrypted with KMS the same way, and read with one API call.
#
# The values are NOT in Terraform. `terraform apply` creates empty
# placeholders and the real values are written afterwards with the AWS CLI
# (see infra/README.md). Putting them in .tfvars would put every API key in
# terraform.tfstate in plaintext — state files are the classic place
# credentials leak from, and the `ignore_changes` below is what stops a
# later apply from wiping the values you set out of band.

locals {
  secret_names = {
    openai_api_key    = "/pta/${var.environment}/OPENAI_API_KEY"
    alpaca_api_key    = "/pta/${var.environment}/ALPACA_API_KEY"
    alpaca_secret_key = "/pta/${var.environment}/ALPACA_SECRET_KEY"
    database_url      = "/pta/${var.environment}/DATABASE_URL"
  }
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.secret_names

  name        = each.value
  description = "paper-trading-agent ${each.key} — value set out of band, see infra/README.md"
  type        = "SecureString"
  value       = "PLACEHOLDER-set-me-with-the-aws-cli"

  lifecycle {
    # Without this, every apply after you set the real value would reset it
    # to the placeholder above and the next scheduled run would fail with an
    # authentication error that looks nothing like its actual cause.
    ignore_changes = [value]
  }
}

# The database password is the one secret Terraform does generate, because
# nothing outside this stack ever needs to know it — the instance reads it
# from DATABASE_URL. It still lands in state, which is why the README says
# to keep state in S3 with encryption on rather than on a laptop.
resource "random_password" "db" {
  count = var.use_rds ? 1 : 0

  # RDS rejects several punctuation characters in master passwords, and a
  # 32-character alphanumeric is plenty of entropy without them.
  length  = 32
  special = false
}
