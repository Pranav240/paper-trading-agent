# Phase 06 — scheduled run on AWS

Terraform for the deployed version of the agent: one EC2 instance running
the container image, a Postgres to write to, secrets in SSM, and an
EventBridge schedule that fires one decision cycle per weekday.

**Nothing here has been applied.** It is written, formatted and validated in
CI, and has never been run against a real AWS account. That is stated
plainly rather than implied, because "the Terraform exists" and "the stack
works" are different claims and only the first one is currently true.

## What it creates

| Resource | Why |
|---|---|
| VPC, 2 public subnets, IGW | Own network rather than the default VPC. No NAT gateway — see below. |
| EC2 `t3.micro` (AL2023) | Runs the container. Docker installed by `user_data.sh.tftpl`. |
| ECR repository | Image registry, with a lifecycle rule keeping the last 10 images. |
| SSM SecureString parameters | API keys and the connection string. |
| IAM instance role | Pull from this one ECR repo, read these four parameters, be managed by SSM. |
| EventBridge Scheduler → SSM RunCommand | The daily trigger. Disabled by default. |
| CloudWatch log group | Run output, 30-day retention. |
| RDS Postgres | **Only if `use_rds = true`.** Off by default. |
| GitHub OIDC role | **Only if `github_repository` is set.** Keyless pushes to ECR. |

## Cost

Rough monthly figures for ap-south-1, assuming free-tier credits are gone.
Check your own console rather than trusting these.

| Item | Cost |
|---|---|
| `t3.micro`, always on | ~$7.50 |
| Public IPv4 address | ~$3.60 |
| 20 GB gp3 root volume | ~$1.80 |
| ECR storage, 10 images | well under $1 |
| SSM Parameter Store (Standard) | $0 |
| EventBridge Scheduler | $0 at this volume |
| **Subtotal** | **~$13/month** |
| RDS `db.t4g.micro`, if enabled | **+$12–15/month** |

Two things not in that table: the OpenAI spend, which is the real running
cost of an armed schedule, and any free-tier credits that would reduce all
of the above to roughly zero.

**Cheapest honest option:** stop the instance when you are not using it.
Storage still bills, compute does not.

## Deliberate omissions

- **No NAT gateway.** ~$32/month, more than everything else combined, for a
  job that makes a handful of outbound calls a day. The instance sits in a
  public subnet and egresses through the internet gateway, which is free.
  Correct at this size, wrong at production scale.
- **No load balancer, no autoscaling, no Multi-AZ.** One instance running a
  daily batch job. Availability engineering for a workload where a day of
  downtime costs nothing would be theatre.
- **No inbound rules by default.** Access is via SSM Session Manager, which
  needs no open port, no SSH key and no bastion.
- **Local Terraform state.** Fine for one person. Move it to an encrypted S3
  bucket with DynamoDB locking before a second person or a CI pipeline ever
  runs `apply` — the RDS password lives in state.

## Applying it

Needs the [AWS CLI](https://aws.amazon.com/cli/) and
[Terraform](https://developer.hashicorp.com/terraform/install), neither of
which is installed on the development machine yet.

```bash
aws configure                 # credentials for your account
cd infra
cp terraform.tfvars.example terraform.tfvars   # then edit it
terraform init
terraform plan                # read this before applying
terraform apply
```

`terraform output next_steps` then prints the exact commands for the rest:
set the four secrets, push an image, reboot so bootstrap picks the secrets
up, run the job once by hand, and only then arm the schedule.

### Order matters

The first `apply` produces an instance whose bootstrap could not do very
much: the SSM parameters still hold placeholders and ECR is empty. That is
expected and the script says so in its log rather than failing. Set the
secrets, push an image, then reboot.

### Verifying before arming anything

```bash
aws ssm start-session --target <instance_id>
sudo tail -50 /var/log/pta-bootstrap.log
sudo /opt/pta/run-agent.sh
```

A successful run prints a `run_id` and one line per symbol. `run_once.py`
exits non-zero on a failed cycle, which surfaces as a Failed SSM invocation
rather than a silent no-op.

**Only then** set `schedule_enabled = true`. Once armed it spends OpenAI
budget every weekday whether or not anyone reads the output.

## Tearing it down

```bash
terraform destroy
```

Removes everything including the database and its data. The backtests are
reproducible from the code and the FNSPID import, so nothing irreplaceable
is lost — but it is a real delete, not a stop.

## What this deployment does not claim

The strategy has no edge. Seven backtests, six of them underperforming
buy-and-hold — see the repository README. Phase 06 demonstrates deploying
and scheduling a containerised agent on AWS; it does not demonstrate a
profitable one, and running it daily will not make it profitable. Deploying
it anyway is a reasonable thing to do for the deployment's own sake, as long
as that distinction stays explicit.
