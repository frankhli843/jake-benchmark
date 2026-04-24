FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssh-client \
    bash \
    jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /harness

# Copy harness files
COPY scripts/ scripts/
COPY tasks.json .
COPY entrypoint.sh .

# Create state directory for dedup persistence
RUN mkdir -p state && chmod +x scripts/*.sh entrypoint.sh

# Default: run scan + QA
ENTRYPOINT ["./entrypoint.sh"]
CMD ["scan-and-qa"]
