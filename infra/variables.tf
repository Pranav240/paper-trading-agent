variable "region" {
  description = "AWS region. ap-south-1 (Mumbai) is the closest region to the developer, which matters for console latency and nothing else — the workload is a once-a-day batch job with no latency requirement."
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Environment name, used in resource names and tags."
  type        = string
  default     = "dev"
}

variable "instance_type" {
  description = "t3.micro is the free-tier-eligible size and is comfortably enough: the workload is one batch run per day that spends most of its wall time waiting on API responses, not computing."
  type        = string
  default     = "t3.micro"
}

# ---------------------------------------------------------------------------
# The cost switch. Read the comment before flipping it.
# ---------------------------------------------------------------------------
variable "use_rds" {
  description = <<-EOT
    false (default): Postgres runs as a container on the same EC2 box, exactly
    as it does on the developer's machine. Costs nothing extra.

    true: a managed RDS instance instead. Better practice for anything real —
    automated backups, point-in-time restore, patching, and a database that
    survives the instance being replaced — but db.t4g.micro is roughly $12-15
    a month once free-tier credits are gone.

    The switch exists because those are genuinely different trade-offs rather
    than a right and a wrong answer, and because the cost should be a decision
    made at apply time by whoever is paying, not baked into the code.
  EOT
  type        = bool
  default     = false
}

variable "db_instance_class" {
  description = "Only used when use_rds is true."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_name" {
  type    = string
  default = "paper_trading_agent"
}

variable "db_username" {
  type    = string
  default = "pta"
}

# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
variable "admin_cidr" {
  description = <<-EOT
    CIDR allowed to reach the instance directly (SSH, and the API port if
    enabled). Defaults to a value that blocks everything, deliberately: an
    accidental 0.0.0.0/0 on port 22 is how personal AWS accounts end up mining
    somebody else's cryptocurrency. Set it to "<your.ip>/32" in
    terraform.tfvars, or leave it closed and use SSM Session Manager, which is
    wired up below and needs no inbound rule at all.
  EOT
  type        = string
  default     = "127.0.0.1/32"
}

variable "expose_api" {
  description = "Open port 8000 to admin_cidr so the FastAPI control plane is reachable. Off by default — the scheduled run needs no inbound traffic whatsoever, and the smallest attack surface is no open port."
  type        = bool
  default     = false
}

variable "key_pair_name" {
  description = "Optional existing EC2 key pair for SSH. Leave empty to have no SSH key at all and rely on SSM Session Manager — fewer secrets to lose."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
variable "schedule_expression" {
  description = "When the daily agent run fires. Default is 21:30 UTC, which is 15 minutes after the US market close (16:00 America/New_York during EDT) — the run reads that day's closing bar, so firing before the close would decide on incomplete data."
  type        = string
  default     = "cron(30 21 ? * MON-FRI *)"
}

variable "schedule_enabled" {
  description = "Whether the schedule is armed. Defaults to false so that `terraform apply` never silently starts spending OpenAI budget on a daily cadence — arming it is a separate, deliberate act."
  type        = bool
  default     = false
}

variable "image_tag" {
  description = "Container image tag to run. CI pushes both `latest` and the commit SHA; pinning a SHA here makes the deployed version explicit rather than whatever `latest` happened to mean."
  type        = string
  default     = "latest"
}
