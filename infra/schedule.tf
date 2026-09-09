# The daily trigger.
#
# EventBridge Scheduler -> SSM RunCommand -> /opt/pta/run-agent.sh on the
# instance, rather than a crontab entry on the box.
#
# A crontab would be simpler and would work. This is chosen because the
# schedule then lives in version control with everything else: what time it
# fires, whether it is armed, and who is allowed to fire it are all visible
# in `terraform plan` instead of being invisible state inside a machine
# someone has to SSH into to inspect. It also means a failed run shows up as
# a Failed invocation in the SSM console with the command output attached,
# instead of a line in /var/log that nobody reads.

resource "aws_iam_role" "scheduler" {
  name = "pta-${var.environment}-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Without this condition any other account able to guess the role ARN
      # could ask EventBridge Scheduler to assume it — the confused-deputy
      # problem. Pinning the source account closes it.
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_runcommand" {
  name = "send-command"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Scoped to this one instance, not "*". A role that can run
        # arbitrary shell on every instance in the account is a much larger
        # thing to hand to a scheduler than it needs to be.
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.app.id}"
      },
      {
        # The document itself is an AWS-owned resource and has to be allowed
        # separately from the instance it runs on.
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ssm:${var.region}::document/AWS-RunShellScript"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "runs" {
  name              = "/pta/${var.environment}/runs"
  retention_in_days = 30 # Never-expiring log groups are a slow leak. Thirty
                         # days is longer than anyone has ever gone back to
                         # look at a daily batch job.
}

resource "aws_scheduler_schedule" "daily_run" {
  name       = "pta-${var.environment}-daily-run"
  group_name = "default"
  state      = var.schedule_enabled ? "ENABLED" : "DISABLED"

  # Off by default, and worth being explicit about why: every firing of this
  # schedule spends real OpenAI money, two calls per watchlist symbol. A
  # `terraform apply` that silently began a daily spend would be a bad
  # surprise, so arming it is a separate decision recorded in tfvars.

  flexible_time_window {
    # A 15-minute window lets AWS spread load. Harmless here — the job reads
    # a closing price that stopped changing hours earlier.
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 15
  }

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ssm:sendCommand"
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      DocumentName = "AWS-RunShellScript"
      InstanceIds  = [aws_instance.app.id]
      Parameters = {
        commands = ["/opt/pta/run-agent.sh"]
      }
      CloudWatchOutputConfig = {
        CloudWatchLogGroupName  = aws_cloudwatch_log_group.runs.name
        CloudWatchOutputEnabled = true
      }
    })

    retry_policy {
      # One retry, an hour apart. The realistic failure is a transient
      # Alpaca or OpenAI error; retrying immediately would hit the same
      # outage, and retrying many times would multiply the cost of a run
      # that is failing for a reason retries cannot fix.
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 3600
    }
  }
}
