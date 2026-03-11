import json
import logging
import os

import boto3
from botocore.exceptions import ClientError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

SECRET_NAME = os.environ.get("APP_SECRET_NAME", "amzn-stock-agent/app-config")


def _load_secret_into_env() -> None:
    """Pull config from Secrets Manager and inject into os.environ if not already set."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    try:
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=SECRET_NAME)
        secret = json.loads(resp["SecretString"])
        for key, value in secret.items():
            if key not in os.environ:
                os.environ[key] = str(value)
        logger.info(f"Loaded {len(secret)} config values from Secrets Manager ({SECRET_NAME})")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "AccessDeniedException"):
            logger.info("Secrets Manager config not found — using env vars only")
        else:
            logger.warning(f"Secrets Manager error ({code}): {e}")
    except Exception as e:
        logger.warning(f"Could not load Secrets Manager config: {e}")


# Load secrets before Settings is instantiated
_load_secret_into_env()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS
    aws_region: str = "us-east-1"
    aws_account_id: str = ""

    # Cognito
    cognito_user_pool_id: str
    cognito_app_client_id: str
    cognito_region: str = "us-east-1"

    # Bedrock
    bedrock_model_id: str = "us.anthropic.claude-3-5-haiku-20241022-v1:0"

    # Langfuse
    langfuse_secret_key: str
    langfuse_public_key: str
    langfuse_host: str = "https://cloud.langfuse.com"

    # Alpha Vantage (free stock API — fallback when Yahoo Finance is blocked)
    alpha_vantage_api_key: str = ""

    # S3
    s3_bucket_name: str = ""

    # RAG
    rag_data_dir: str = "data/amazon_reports"
    faiss_index_path: str = "data/faiss_index"
    chunk_size: int = 1000
    chunk_overlap: int = 100


settings = Settings()
