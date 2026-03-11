variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication"
  type        = string
  default     = "floba-dev"
}

variable "project_name" {
  description = "Project prefix used for naming all resources"
  type        = string
  default     = "amzn-stock-agent"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
}

# --- Cognito ---

variable "cognito_user_pool_name" {
  description = "Name of the Cognito user pool"
  type        = string
  default     = "amzn-stock-agent-users"
}

variable "cognito_app_client_name" {
  description = "Name of the Cognito app client"
  type        = string
  default     = "amzn-stock-agent-client"
}

# --- ECR ---

variable "ecr_repo_name" {
  description = "Name of the ECR repository for the agent container"
  type        = string
  default     = "amzn-stock-agent"
}

variable "ecr_image_tag_mutability" {
  description = "Image tag mutability for ECR (MUTABLE | IMMUTABLE)"
  type        = string
  default     = "MUTABLE"
}

# --- S3 ---

variable "s3_bucket_name" {
  description = "S3 bucket name for document storage (must be globally unique)"
  type        = string
  default     = ""
}

# --- Alpha Vantage ---

variable "alpha_vantage_api_key" {
  description = "Alpha Vantage API key for stock price data (free at alphavantage.co)"
  type        = string
  sensitive   = true
  default     = "AY4BWXK9BXC8VG4O"
}

# --- Langfuse (injected as container env vars) ---

variable "langfuse_secret_key" {
  description = "Langfuse secret key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "langfuse_public_key" {
  description = "Langfuse public key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "langfuse_host" {
  description = "Langfuse host URL"
  type        = string
  default     = "https://us.cloud.langfuse.com"
}

# --- AgentCore ---

variable "agentcore_runtime_name" {
  description = "Name for the AgentCore runtime"
  type        = string
  default     = "amzn_stock_agent_runtime"
}

variable "agentcore_container_port" {
  description = "Port the FastAPI container listens on (AgentCore requires 8080)"
  type        = number
  default     = 8080
}

variable "agentcore_memory_size_mb" {
  description = "Memory allocation for AgentCore runtime (MB)"
  type        = number
  default     = 2048
}
