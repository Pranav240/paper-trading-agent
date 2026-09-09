# Terraform rather than CloudFormation or CDK: this is the format most
# widely read outside AWS, and the whole point of Phase 06 is to show the
# deployment, not just to have one running.
#
# Versions are pinned with `~>` rather than left open. An unpinned provider
# means `terraform init` six months from now can produce a plan that
# destroys and recreates resources because a default changed upstream —
# the same "reproducible or it didn't happen" reasoning behind pinning
# requirements.txt.

terraform {
  required_version = "~> 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "paper-trading-agent"
      Phase       = "06-scheduled-run"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
