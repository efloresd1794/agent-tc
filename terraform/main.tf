terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  bucket_name = var.s3_bucket_name != "" ? var.s3_bucket_name : "${var.project_name}-docs-${local.account_id}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# =============================================================================
# ECR Repository
# =============================================================================

resource "aws_ecr_repository" "agent" {
  name                 = var.ecr_repo_name
  image_tag_mutability = var.ecr_image_tag_mutability

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# =============================================================================
# S3 Bucket (document storage)
# =============================================================================

resource "aws_s3_bucket" "documents" {
  bucket        = local.bucket_name
  force_destroy = true

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# =============================================================================
# Cognito User Pool
# =============================================================================

resource "aws_cognito_user_pool" "main" {
  name = var.cognito_user_pool_name

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = local.common_tags
}

resource "aws_cognito_user_pool_client" "main" {
  name         = var.cognito_app_client_name
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = false
  explicit_auth_flows                  = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 1
  id_token_validity                    = 1
  refresh_token_validity               = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

# =============================================================================
# IAM — AgentCore Execution Role
# =============================================================================

data "aws_iam_policy_document" "agentcore_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agentcore_execution" {
  name               = "${var.project_name}-agentcore-execution-role"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json

  tags = local.common_tags
}

data "aws_iam_policy_document" "agentcore_permissions" {
  # ECR — pull container image
  statement {
    effect = "Allow"
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  # Bedrock — invoke foundation models (direct)
  statement {
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      # Foundation models (direct invocation)
      "arn:aws:bedrock:${var.aws_region}::foundation-model/*",
      # Cross-region inference profiles (us.anthropic.*, eu.*, ap.*)
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/*",
    ]
  }

  # Bedrock — list and get inference profiles
  statement {
    effect = "Allow"
    actions = [
      "bedrock:GetInferenceProfile",
      "bedrock:ListInferenceProfiles",
    ]
    resources = ["*"]
  }

  # AgentCore — allow container to communicate with AgentCore service
  statement {
    effect = "Allow"
    actions = [
      "bedrock-agentcore:*",
    ]
    resources = ["*"]
  }

  # Secrets Manager — read app config at container startup
  statement {
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${var.project_name}/app-config*"]
  }

  # S3 — read documents
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.documents.arn,
      "${aws_s3_bucket.documents.arn}/*",
    ]
  }

  # CloudWatch Logs
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock/agentcore/*",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/*",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:*",
    ]
  }
}

# =============================================================================
# CloudWatch Log Group — AgentCore container logs
# =============================================================================

resource "aws_cloudwatch_log_group" "agentcore" {
  # AgentCore writes logs to: /aws/bedrock-agentcore/runtimes/<runtime_id>-<endpoint_name>/[runtime-logs]
  # Pre-create the parent group so the service can create streams under it.
  name              = "/aws/bedrock-agentcore/runtimes"
  retention_in_days = 3

  tags = local.common_tags
}

resource "aws_iam_role_policy" "agentcore_permissions" {
  name   = "${var.project_name}-agentcore-permissions"
  role   = aws_iam_role.agentcore_execution.id
  policy = data.aws_iam_policy_document.agentcore_permissions.json
}

# =============================================================================
# Secrets Manager — app config for AgentCore container
# =============================================================================

resource "aws_secretsmanager_secret" "app_config" {
  name                    = "${var.project_name}/app-config"
  recovery_window_in_days = 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "app_config" {
  secret_id = aws_secretsmanager_secret.app_config.id
  secret_string = jsonencode({
    COGNITO_USER_POOL_ID     = aws_cognito_user_pool.main.id
    COGNITO_APP_CLIENT_ID    = aws_cognito_user_pool_client.main.id
    COGNITO_REGION           = var.aws_region
    S3_BUCKET_NAME           = aws_s3_bucket.documents.bucket
    BEDROCK_MODEL_ID         = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    AWS_REGION               = var.aws_region
    LANGFUSE_SECRET_KEY      = var.langfuse_secret_key
    LANGFUSE_PUBLIC_KEY      = var.langfuse_public_key
    LANGFUSE_HOST            = var.langfuse_host
    ALPHA_VANTAGE_API_KEY    = var.alpha_vantage_api_key
  })
}

# =============================================================================
# S3 — Upload pre-built FAISS index
# =============================================================================

resource "aws_s3_object" "faiss_index" {
  bucket = aws_s3_bucket.documents.id
  key    = "faiss_index/index.faiss"
  source = "${path.module}/../data/faiss_index/index.faiss"
  etag   = filemd5("${path.module}/../data/faiss_index/index.faiss")
  tags   = local.common_tags
}

resource "aws_s3_object" "faiss_pkl" {
  bucket = aws_s3_bucket.documents.id
  key    = "faiss_index/index.pkl"
  source = "${path.module}/../data/faiss_index/index.pkl"
  etag   = filemd5("${path.module}/../data/faiss_index/index.pkl")
  tags   = local.common_tags
}

# NOTE: AgentCore Runtime is defined in agentcore_runtime.tf
# It must be applied AFTER the Docker image is pushed to ECR.
# See README.md — Deployment Steps.
