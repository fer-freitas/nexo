# Coletor central e portal do Nexo na AWS: Fargate, RDS e balanceador interno, sem endereço público.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

locals {
  name = "nexo-central-${var.environment}"
  tags = { service = "nexo-central", environment = var.environment, owner = "time-do-nexo" }
}

resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  vpc_id      = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.internal_cidrs
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "app" {
  name_prefix = "${local.name}-app-"
  vpc_id      = var.vpc_id
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "db" {
  name_prefix = "${local.name}-db-"
  vpc_id      = var.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  tags = local.tags
}

resource "aws_lb" "this" {
  name               = local.name
  internal           = true # sem exposição à internet
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.private_subnet_ids
  tags               = local.tags
}

resource "aws_lb_target_group" "app" {
  name        = local.name
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  health_check {
    path    = "/health"
    matcher = "200"
  }
  tags = local.tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
  tags       = local.tags
}

resource "aws_db_instance" "this" {
  identifier                = local.name
  engine                    = "postgres"
  engine_version            = "16"
  instance_class            = var.db_instance_class
  allocated_storage         = 20
  max_allocated_storage     = 100
  storage_encrypted         = true
  db_name                   = "nexo"
  username                  = "nexo"
  manage_master_user_password = true
  multi_az                  = var.environment == "prod"
  backup_retention_period   = 14
  deletion_protection       = var.environment == "prod"
  skip_final_snapshot       = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "${local.name}-final" : null
  db_subnet_group_name      = aws_db_subnet_group.this.name
  vpc_security_group_ids    = [aws_security_group.db.id]
  publicly_accessible       = false
  tags                      = local.tags
}

resource "aws_secretsmanager_secret" "app" {
  name = "${local.name}/app"
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 90
  tags              = local.tags
}

resource "aws_iam_role" "execution" {
  name_prefix = "${local.name}-exec-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "secrets" {
  name = "secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.app.arn, aws_db_instance.this.master_user_secret[0].secret_arn]
    }]
  })
}

resource "aws_ecs_cluster" "this" {
  name = local.name
  tags = local.tags
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name         = "central"
    image        = var.image
    essential    = true
    portMappings = [{ containerPort = 8080 }]
    environment = [
      { name = "NX_COLLECTOR_PUBLIC_URL", value = var.public_url },
      { name = "NX_COLLECTOR_DB_HOST", value = aws_db_instance.this.address },
      { name = "NX_COLLECTOR_DB_NAME", value = "nexo" },
    ]
    secrets = [
      { name = "NX_COLLECTOR_PSEUDO_KEY", valueFrom = "${aws_secretsmanager_secret.app.arn}:pseudo_key::" },
      { name = "NX_COLLECTOR_TOKENS", valueFrom = "${aws_secretsmanager_secret.app.arn}:tokens::" },
      { name = "NX_COLLECTOR_DB_USER", valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:username::" },
      { name = "NX_COLLECTOR_DB_PASSWORD", valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "central"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "app" {
  name            = local.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.app.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "central"
    container_port   = 8080
  }
  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "http_5xx" {
  alarm_name          = "${local.name}-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  alarm_actions       = var.alarm_topic_arns
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "db_connections" {
  alarm_name          = "${local.name}-db-connections"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = var.alarm_topic_arns
  tags                = local.tags
}

# DORA pela API do GitHub, uma vez por dia, como tarefa do próprio central
resource "aws_iam_role" "scheduler" {
  name = "${local.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "run-dora"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecs:RunTask", Resource = aws_ecs_task_definition.app.arn },
      { Effect = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.execution.arn },
    ]
  })
}

resource "aws_scheduler_schedule" "dora" {
  name                = "${local.name}-dora"
  schedule_expression = var.dora_schedule
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler.arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.app.arn
      launch_type         = "FARGATE"
      network_configuration {
        subnets         = var.private_subnet_ids
        security_groups = [aws_security_group.app.id]
      }
    }
    input = jsonencode({
      containerOverrides = [{
        name        = "central"
        command     = ["python", "-m", "nexo.central.jobs", "dora"]
        environment = [{ name = "NX_DORA_REPOS", value = join(",", var.dora_repos) }]
      }]
    })
  }
}
