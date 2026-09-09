# Everything here is created only when use_rds = true. With the default
# (false), Postgres runs as a container on the instance — see
# user_data.sh.tftpl — and this file produces no resources at all.

resource "aws_db_subnet_group" "main" {
  count = var.use_rds ? 1 : 0

  name       = "pta-${var.environment}"
  subnet_ids = aws_subnet.public[*].id

  tags = { Name = "pta-${var.environment}-db-subnets" }
}

resource "aws_db_instance" "main" {
  count = var.use_rds ? 1 : 0

  identifier     = "pta-${var.environment}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db[0].result

  # Storage autoscaling off on purpose: this database holds a few hundred
  # thousand rows, and silent growth is silent billing.
  allocated_storage     = 20
  max_allocated_storage = 0
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main[0].name
  vpc_security_group_ids = [aws_security_group.db[0].id]

  # The app instance reaches this over the VPC. Nothing on the internet
  # should be able to open a socket to it.
  publicly_accessible = false

  # Single AZ. A day of downtime on a paper-trading side project costs
  # nothing, and Multi-AZ doubles the bill.
  multi_az = false

  backup_retention_period = 7

  # Nothing here is irreplaceable: backtests are reproducible from the code
  # and the FNSPID import. Set this false and add a
  # final_snapshot_identifier if that ever stops being true.
  skip_final_snapshot = true
  deletion_protection = false

  # Minor version patches applied in the maintenance window rather than
  # never. An unpatched database is a worse risk than a two-minute restart
  # on a workload that runs once a day.
  auto_minor_version_upgrade = true

  tags = { Name = "pta-${var.environment}-db" }
}
