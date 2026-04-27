FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create directory for SQLite database
RUN mkdir -p /app/data

# Environment variables
ENV APP_PORT=8000
ENV LOG_LEVEL=INFO
ENV OLLAMA_BASE_URL=http://ollama:11434
ENV OLLAMA_OPENAI_URL=http://ollama:11434/v1
ENV DATABASE_PATH=/app/data/ai_hub.db

EXPOSE 8000

# Start application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
