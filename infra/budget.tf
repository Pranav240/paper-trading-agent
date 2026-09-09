# A spend alarm, because this stack has no free-tier cover.
#
# The account has $0 in credits and no Free Tier allowances showing, so
# every resource here bills from the first hour. That is fine for a
# deliberate short-lived test and expensive for a stack somebody forgets
# about — and forgetting is the normal failure mode for a side project,
# not an unlikely one.
#
# AWS Budgets gives two budgets free per account, so this costs nothing.
# It cannot stop spending; it can only tell you it is happening. That is
# still the difference between noticing in a day and noticing on a
# statement.
#
# Set alert_email in terraform.tfvars to switch it on.

variable "alert_email" {
  description = "Email for budget alerts. Empty disables the budget entirely. Note that AWS sends a subscription confirmation you have to click before any alert can reach you."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly spend threshold for the alert. Default 20 against an expected ~13 — high enough not to cry wolf, low enough that a runaway resource trips it in days rather than at month end."
  type        = number
  default     = 20
}

resource "aws_budgets_budget" "monthly" {
  count = var.alert_email == "" ? 0 : 1

  name         = "pta-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Two notifications, on purpose, because they answer different questions.
  #
  # ACTUAL at 50%: "money is being spent" — the one that catches a stack
  # left running by accident, which is the realistic risk here.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # FORECASTED at 100%: "at this rate you will blow the budget" — arrives
  # early enough to act on, rather than confirming an overspend that has
  # already happened.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
