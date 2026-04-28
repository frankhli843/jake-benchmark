FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssh-client \
    bash \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Python runtime deps for the harness library + CLI.
RUN pip install --no-cache-dir jsonschema==4.23.0

WORKDIR /harness

# Copy harness files
COPY scripts/ scripts/
COPY lib/ lib/
COPY schemas/ schemas/
COPY tasks.json .
COPY tasks-pack-v1.json .
COPY entrypoint.sh .

# Create state directory for dedup persistence
RUN mkdir -p state && chmod +x scripts/*.sh entrypoint.sh

# Default: run scan + QA
ENTRYPOINT ["./entrypoint.sh"]
CMD ["scan-and-qa"]
