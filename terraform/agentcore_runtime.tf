# =============================================================================
# AgentCore Runtime (container-based)
#
# IMPORTANT: Apply this AFTER the Docker image has been pushed to ECR.
#
#   Step 1:  terraform apply -target=module  (or full apply minus this file)
#   Step 2:  docker build + push to ECR
#   Step 3:  terraform apply   (picks up this resource)
# =============================================================================

resource "aws_bedrockagentcore_agent_runtime" "stock_agent" {
  agent_runtime_name = var.agentcore_runtime_name
  role_arn           = aws_iam_role.agentcore_execution.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.agent.repository_url}:latest"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  depends_on = [
    aws_ecr_repository.agent,
    aws_iam_role_policy.agentcore_permissions,
    aws_cloudwatch_log_group.agentcore,
  ]
}

resource "aws_bedrockagentcore_agent_runtime_endpoint" "default" {
  agent_runtime_id = aws_bedrockagentcore_agent_runtime.stock_agent.agent_runtime_id
  name             = "DEFAULT"

  depends_on = [aws_bedrockagentcore_agent_runtime.stock_agent]
}
