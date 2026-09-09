# ---------------------------------------------------------------------------
# Container registry
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name                 = "paper-trading-agent"
  image_tag_mutability = "MUTABLE" # CI moves `latest`; immutable tags would
                                   # make that push fail every time.

  image_scanning_configuration {
    scan_on_push = true
  }

  # The image is rebuildable from the Dockerfile in one command, so keeping
  # the registry after a teardown protects nothing and costs storage.
  force_delete = true
}

# ECR bills per GB-month, and CI pushes a new image on every commit to
# master. Without expiry the repository grows forever and quietly becomes
# the largest line on the bill for a project whose compute is one batch job
# a day.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# Instance role
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "instance" {
  name = "pta-${var.environment}-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# SSM managed policy buys two things at once: Session Manager shell access
# with no SSH key, no open port 22 and no bastion, and the RunCommand
# channel the scheduler uses to start the daily job. It is the reason this
# stack can have zero inbound rules by default.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Scoped by hand rather than attaching AmazonEC2ContainerRegistryReadOnly:
# that managed policy grants pull on every repository in the account, and
# this instance only ever needs one. GetAuthorizationToken is unavoidably
# account-wide — the API takes no resource.
resource "aws_iam_role_policy" "ecr_pull" {
  name = "ecr-pull"
  role = aws_iam_role.instance.id

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
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = aws_ecr_repository.app.arn
      },
    ]
  })
}

resource "aws_iam_role_policy" "read_secrets" {
  name = "read-secrets"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:GetParameters"]
      Resource = [for p in aws_ssm_parameter.secret : p.arn]
    }]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "pta-${var.environment}-instance"
  role = aws_iam_role.instance.name
}

# ---------------------------------------------------------------------------
# The instance
# ---------------------------------------------------------------------------

# Resolved at plan time rather than hardcoded: an AMI id is region-specific
# and goes stale every time Amazon publishes a patched image.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

locals {
  db_url = var.use_rds ? "postgresql://${var.db_username}:${random_password.db[0].result}@${aws_db_instance.main[0].address}:5432/${var.db_name}" : "postgresql://${var.db_username}:local_dev_password@127.0.0.1:5432/${var.db_name}"

  image_uri = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  key_name               = var.key_pair_name == "" ? null : var.key_pair_name

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region          = var.region
    account_id      = data.aws_caller_identity.current.account_id
    image_uri       = local.image_uri
    use_rds         = var.use_rds
    db_name         = var.db_name
    db_username     = var.db_username
    ssm_prefix      = "/pta/${var.environment}"
    ecr_registry    = split("/", aws_ecr_repository.app.repository_url)[0]
    expose_api      = var.expose_api
  })

  # user_data changes rebuild the box rather than being silently ignored,
  # which is what makes this stack reproducible instead of a pet.
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 20 # 8GB is the AL2023 default and does not survive a few
                     # image pulls; gp3 at 20GB is well inside free tier.
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 only. IMDSv1 is how instance
                             # credentials get exfiltrated through SSRF.
  }

  tags = { Name = "pta-${var.environment}-app" }
}
