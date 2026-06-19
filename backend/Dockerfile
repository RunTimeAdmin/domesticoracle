# syntax=docker/dockerfile:1
# Build context: project root (D:\EchoBond)
FROM python:3.12-slim

WORKDIR /app

# gcc / libffi needed by cryptography; git needed by some langchain dep resolvers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Ora dependencies first (better layer caching)
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Install vendored DeerFlow harness
COPY backend/vendor/deerflow-harness /tmp/deerflow-harness
RUN pip install --no-cache-dir /tmp/deerflow-harness

# Copy application source
COPY backend/ .

# Runtime data (oracle.db, oracle_keys/, anchor.log, oracle_memory.json, .deer-flow/)
# lives in a mounted volume so it survives container rebuilds.
# ORA_DATA_DIR is picked up by db.py, crypto.py, and ledger.py.
# DEER_FLOW_PROJECT_ROOT is picked up by domestic_oracle/agent.py via setdefault.
ENV ORA_DATA_DIR=/data
ENV DEER_FLOW_PROJECT_ROOT=/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
