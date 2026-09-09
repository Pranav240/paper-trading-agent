# Lets GitHub Actions push images to ECR without a long-lived access key.
#
# The obvious alternative is an IAM user with an access key pasted into
# GitHub secrets. That key is valid until someone remembers to rotate it,
# works from anywhere on the internet, and is one leaked workflow log away
# from being someone else's. OIDC instead has GitHub mint a short-lived
# token per job, which AWS trades for temporary credentials that expire in
# an hour and only work for the repository named in the trust policy below.
#
# Set github_repository to "owner/repo" to switch this on.

variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role, e.g. \"Pranav240/paper-trading-agent\". Empty disables OIDC entirely."
  type        = string
  default     = ""
}

variable "create_github_oidc_provider" {
  description = "Create the account-level GitHub OIDC provider. Exactly one can exist per AWS account, so set this false if another stack already created it."
  type        = bool
  default     = true
}

locals {
  oidc_enabled = var.github_repository != ""
}

resource "aws_iam_openid_connect_provider" "github" {
  count = local.oidc_enabled && var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS stopped requiring an accurate thumbprint for this provider in 2023 —
  # it validates GitHub's certificate chain itself — but the field is still
  # mandatory. This is GitHub's long-published value.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = local.oidc_enabled && !var.create_github_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "github_deploy" {
  count = local.oidc_enabled ? 1 : 0

  name = "pta-${var.environment}-github-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # `repo:owner/name:*` and not a bare wildcard. Without a `sub`
        # condition, ANY GitHub repository on the internet could assume this
        # role — the single most common way OIDC gets misconfigured.
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:*"
        }
      }
    }]
  })
}

# Push to this one repository, and nothing else. Deliberately not
# AmazonEC2ContainerRegistryPowerUser, which grants write on every registry
# in the account to anything that can run a workflow.
resource "aws_iam_role_policy" "github_deploy_ecr" {
  count = local.oidc_enabled ? 1 : 0

  name = "ecr-push"
  role = aws_iam_role.github_deploy[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = aws_ecr_repository.app.arn
      },
    ]
  })
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable in GitHub."
  value       = local.oidc_enabled ? aws_iam_role.github_deploy[0].arn : "OIDC disabled - set github_repository to enable"
}
