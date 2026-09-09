# A small purpose-built VPC rather than the default one.
#
# The default VPC would work and would be one line. It is avoided here for
# one reason: the default VPC's subnets are public in every availability
# zone, and "which of these is my thing allowed to talk to" becomes an
# archaeology exercise the moment a second project shares the account.
#
# NOTE ON WHAT IS DELIBERATELY ABSENT: there is no NAT gateway and no
# private subnet. A NAT gateway is ~$32/month plus data processing — more
# than every other resource in this stack combined, for a workload that
# makes a handful of outbound API calls once a day. The instance sits in a
# public subnet with a public IP and reaches the internet through the
# internet gateway, which is free. That is the right trade at this size and
# the wrong one at production scale; it is a cost decision, not an
# oversight.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "pta-${var.environment}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "pta-${var.environment}-igw" }
}

# Two subnets in different AZs. Only one is used by the instance; the second
# exists because RDS refuses to create a subnet group with fewer than two
# availability zones, even for a single-AZ instance. Creating it
# unconditionally keeps `use_rds` a genuine one-line flip rather than a flip
# plus a network change.
resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "pta-${var.environment}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "pta-${var.environment}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "app" {
  name        = "pta-${var.environment}-app"
  description = "Paper trading agent instance"
  vpc_id      = aws_vpc.main.id

  # Egress is open because the whole job is outbound calls: Alpaca for
  # prices, OpenAI for the two LLM nodes, ECR for the image, and SSM for
  # the scheduled trigger. Locking this down to specific prefixes is
  # possible but would need VPC endpoints (~$7/month each) to do properly.
  egress {
    description = "All outbound - Alpaca, OpenAI, ECR, SSM"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "pta-${var.environment}-app" }
}

# Ingress rules are separate resources rather than inline blocks so that
# toggling `expose_api` or `key_pair_name` doesn't force the whole security
# group to be replaced (which would detach it from a running instance).

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  count = var.key_pair_name == "" ? 0 : 1

  security_group_id = aws_security_group.app.id
  description       = "SSH from admin_cidr only"
  cidr_ipv4         = var.admin_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "api" {
  count = var.expose_api ? 1 : 0

  security_group_id = aws_security_group.app.id
  description       = "FastAPI control plane from admin_cidr only"
  cidr_ipv4         = var.admin_cidr
  from_port         = 8000
  to_port           = 8000
  ip_protocol       = "tcp"
}

# Postgres, reachable only from the app security group — never from the
# internet, regardless of what admin_cidr is set to.
resource "aws_security_group" "db" {
  count = var.use_rds ? 1 : 0

  name        = "pta-${var.environment}-db"
  description = "RDS Postgres, reachable only from the app instance"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "pta-${var.environment}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  count = var.use_rds ? 1 : 0

  security_group_id            = aws_security_group.db[0].id
  description                  = "Postgres from the app instance only"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
