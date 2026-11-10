# Single-node Redis, matching the chart: one replica is not HA. A snapshot
# limit of 0 does not keep the queue available during node loss (#216).
# maxmemory-policy volatile-lru matches the chart so TTL-less seen-sets and
# queues are not evicted. Clients use REDIS_HOST, REDIS_PORT, REDIS_PASSWORD,
# and REDIS_DB. This module does not build a Redis URL.

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "Redis 6379 from the stub VPC only"
  vpc_id      = aws_vpc.pipeline.id

  ingress {
    description = "Redis from the stub VPC"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-redis"
  })
}

resource "aws_elasticache_subnet_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "Private subnets for the stub Redis"
  subnet_ids  = aws_subnet.private[*].id

  tags = local.common_tags
}

resource "aws_elasticache_parameter_group" "redis" {
  name        = "${var.name_prefix}-redis7"
  family      = "redis7"
  description = "volatile-lru so TTL-less seen-sets and queues are not evicted"

  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  tags = local.common_tags
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Scraping pipeline Redis (seen-sets and queues). Single node, not HA."

  engine         = "redis"
  engine_version = "7.4"
  node_type      = var.redis_node_type
  port           = 6379

  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false

  parameter_group_name = aws_elasticache_parameter_group.redis.name
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  # Null omits AUTH until a human supplies TF_VAR_redis_auth_token.
  # SET (not ROTATE) so the first token does not leave passwordless access on.
  auth_token                 = var.redis_auth_token
  auth_token_update_strategy = var.redis_auth_token == null ? null : "SET"

  snapshot_retention_limit   = 0
  auto_minor_version_upgrade = false

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-redis"
  })
}
