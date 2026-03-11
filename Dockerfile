FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code only — data/ (PDFs + FAISS index) is loaded from S3 at runtime
COPY app/ ./app/

# AgentCore requires port 8080 (not 8000) and ARM64 architecture.
# Build with: docker build --platform linux/arm64 -t amzn-stock-agent .
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
