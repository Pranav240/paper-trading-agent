output "instance_id" {
  description = "Use with: aws ssm start-session --target <id>"
  value       = aws_instance.app.id
}

output "public_ip" {
  value = aws_instance.app.public_ip
}

output "ecr_repository_url" {
  description = "Push target for the deploy workflow."
  value       = aws_ecr_repository.app.repository_url
}

output "schedule_state" {
  description = "ENABLED means this is spending OpenAI budget every weekday."
  value       = aws_scheduler_schedule.daily_run.state
}

output "database_mode" {
  value = var.use_rds ? "RDS ${var.db_instance_class}" : "container on the instance"
}

output "database_url_parameter" {
  description = "Set this SSM parameter to the value below before the first run."
  value       = aws_ssm_parameter.secret["database_url"].name
}

# Deliberately marked sensitive rather than omitted: it is needed to populate
# the DATABASE_URL parameter, so hiding it entirely would just push someone
# toward reconstructing it by hand and getting it wrong. `terraform output
# -raw database_url` prints it when actually wanted; a bare `terraform
# output` does not splash a password across the terminal.
output "database_url" {
  description = "Connection string for the chosen database mode."
  value       = local.db_url
  sensitive   = true
}

output "next_steps" {
  value = <<-EOT

    1. Put the real secrets in SSM (they are placeholders right now):

         aws ssm put-parameter --region ${var.region} --overwrite --type SecureString \
           --name /pta/${var.environment}/OPENAI_API_KEY --value 'sk-...'
         aws ssm put-parameter --region ${var.region} --overwrite --type SecureString \
           --name /pta/${var.environment}/ALPACA_API_KEY --value '...'
         aws ssm put-parameter --region ${var.region} --overwrite --type SecureString \
           --name /pta/${var.environment}/ALPACA_SECRET_KEY --value '...'
         aws ssm put-parameter --region ${var.region} --overwrite --type SecureString \
           --name /pta/${var.environment}/DATABASE_URL --value "$(terraform output -raw database_url)"

    2. Push an image (GitHub Actions "Deploy to AWS", or by hand - see infra/README.md).

    3. Replace the instance so bootstrap re-runs with the real secrets.
       NOT a reboot: cloud-init runs user_data on an instance's first boot
       only, so rebooting re-runs nothing and /opt/pta/app.env keeps the
       placeholder values it was launched with.

         terraform apply -replace=aws_instance.app

    4. Test one run manually before arming the schedule:

         aws ssm send-command --region ${var.region} \
           --instance-ids ${aws_instance.app.id} \
           --document-name AWS-RunShellScript \
           --parameters 'commands=["/opt/pta/run-agent.sh"]'

    5. Only then set schedule_enabled = true. It spends OpenAI budget every weekday.

    Tear it all down with: terraform destroy
  EOT
}
