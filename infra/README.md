# Phase 06 — scheduled run on AWS

Terraform for the deployed version of the agent: one EC2 instance running
the container image, a Postgres to write to, secrets in SSM, and an
EventBridge schedule that fires one decision cycle per weekday.

**Applied and verified on 2026-09-09, then destroyed.** A t3.micro in
ap-south-1 ran one full decision cycle against real Alpaca prices, real
OpenAI calls and a real Postgres write, triggered through SSM exactly as
the schedule would:

```
run_id      1
status      SUCCESS
decisions   1
  AAPL   BUY  conf=0.65
```

The stack was torn down the same day. Leaving it up costs ~$13/month to
run a strategy already measured as having no edge; the point was to prove
the deployment works, and it does.

### Three bugs that only appeared on a real apply

Worth recording, because every one of them passed `terraform validate` and
would have passed any amount of re-reading:

1. **`DATABASE_URL` pointed at `127.0.0.1`.** Correct for a process on the
   host, wrong for a container: inside a container that address is the
   container's own loopback. `db/bootstrap.py` failed with "Connection
   refused" while `docker ps` showed Postgres up and healthy — both true at
   once. The fix is the container name, `pta-postgres:5432`, resolved by
   Docker's embedded DNS on the shared network.
2. **`most_recent = true` on the AMI data source churned the instance.**
   Amazon published a new AL2023 image an hour after the first apply and
   the next plan proposed destroying and recreating a running instance
   (`ami-0942...49d` -> `ami-090d...756`). Fixed with
   `lifecycle { ignore_changes = [ami] }`: newest AMI at creation, no
   surprise replacements after. A plan you cannot trust is a plan you stop
   reading.
3. **GitHub's OIDC subject claim carries immutable numeric IDs.** The
   documented form is `repo:owner/name:ref:...`; this repository actually
   presents
   `repo:Pranav240@130759083/paper-trading-agent@1350596187:ref:refs/heads/master`.
   A trust policy written against the documented form never matches, and
   STS says only "Not authorized to perform sts:AssumeRoleWithWebIdentity"
   — deliberately uninformative, since naming the failed condition would
   let an attacker probe the policy. CloudTrail carries the actual claim
   and is the intended debugging path. The policy now allows both forms.

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
[Terraform](https://developer.hashicorp.com/terraform/install). On Windows:
`winget install --id Amazon.AWSCLI -e` and
`winget install --id Hashicorp.Terraform -e`, then reopen the terminal so
PATH refreshes.

```bash
aws configure                 # credentials for your account
cd infra
cp terraform.tfvars.example terraform.tfvars   # then edit it
terraform init
terraform plan                # read this before applying
terraform apply
```

`terraform output next_steps` then prints the exact commands for the rest:
set the four secrets, push an image, rebuild the instance, run the job once
by hand, and only then arm the schedule.

### Order matters, and a reboot is not enough

The first `apply` produces an instance whose bootstrap could not do very
much: the SSM parameters still hold placeholders and ECR is empty. That is
expected and the script says so in its log rather than failing.

Set the secrets and push an image, then **replace** the instance:

```bash
terraform apply -replace=aws_instance.app
```

Not a reboot. `user_data` is executed by cloud-init on an instance's *first*
boot only, so rebooting re-runs nothing and leaves `/opt/pta/app.env` full
of whatever the secrets held at launch. Replacing gives a genuine first
boot, which is also the only way to know the bootstrap path actually works
end to end rather than having been hand-patched into place.

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
