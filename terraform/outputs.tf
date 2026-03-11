# =============================================================================
# ECR
# =============================================================================

output "ecr_repository_url" {
  description = "ECR repository URL — use this to tag and push the Docker image"
  value       = aws_ecr_repository.agent.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN"
  value       = aws_ecr_repository.agent.arn
}

# =============================================================================
# Cognito
# =============================================================================

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID — set as COGNITO_USER_POOL_ID in .env"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  description = "Cognito App Client ID — set as COGNITO_APP_CLIENT_ID in .env"
  value       = aws_cognito_user_pool_client.main.id
}

output "cognito_user_pool_endpoint" {
  description = "Cognito User Pool endpoint for JWKS validation"
  value       = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

# =============================================================================
# S3
# =============================================================================

output "s3_bucket_name" {
  description = "S3 bucket name for document storage — set as S3_BUCKET_NAME in .env"
  value       = aws_s3_bucket.documents.bucket
}

output "s3_bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.documents.arn
}

# =============================================================================
# IAM
# =============================================================================

output "agentcore_execution_role_arn" {
  description = "IAM role ARN for AgentCore execution"
  value       = aws_iam_role.agentcore_execution.arn
}

# =============================================================================
# AgentCore
# =============================================================================

output "agentcore_runtime_name" {
  description = "AgentCore runtime name"
  value       = var.agentcore_runtime_name
}

output "app_secret_name" {
  description = "Secrets Manager secret name — set as APP_SECRET_NAME in .env"
  value       = aws_secretsmanager_secret.app_config.name
}

output "agentcore_runtime_arn" {
  description = "AgentCore runtime ARN — set as AGENT_ENDPOINT in .env"
  value       = aws_bedrockagentcore_agent_runtime.stock_agent.agent_runtime_arn
}

output "agentcore_endpoint_arn" {
  description = "AgentCore DEFAULT endpoint ARN"
  value       = aws_bedrockagentcore_agent_runtime_endpoint.default.agent_runtime_endpoint_arn
}

# =============================================================================
# Helper — docker push command
# =============================================================================

output "docker_push_commands" {
  description = "Commands to authenticate and push the Docker image to ECR"
  value = <<-EOT
    aws ecr get-login-password --region ${var.aws_region} | \
      docker login --username AWS --password-stdin ${aws_ecr_repository.agent.repository_url}

    docker build -t ${var.ecr_repo_name} .
    docker tag ${var.ecr_repo_name}:latest ${aws_ecr_repository.agent.repository_url}:latest
    docker push ${aws_ecr_repository.agent.repository_url}:latest
  EOT
}
