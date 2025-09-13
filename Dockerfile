# MacBot Dockerfile
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    ffmpeg \
    portaudio19-dev \
    libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt requirements-dev.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create necessary directories
RUN mkdir -p rag_data rag_database logs

# Build whisper.cpp and llama.cpp (if models are included)
# Note: In production, models should be mounted as volumes
RUN if [ -d "models/whisper.cpp" ]; then \
        cd models/whisper.cpp && \
        cmake -S . -B build -DWHISPER_COREML=0 && \
        cmake --build build -j$(nproc) && \
        cd ..; \
    fi

RUN if [ -d "models/llama.cpp" ]; then \
        cd models/llama.cpp && \
        cmake -S . -B build -DLLAMA_METAL=OFF && \
        cmake --build build -j$(nproc) && \
        cd ..; \
    fi

# Create non-root user
RUN useradd --create-home --shell /bin/bash macbot
RUN chown -R macbot:macbot /app
USER macbot

# Expose ports
EXPOSE 3000 8080 8001 8123 8090 8082

# Health check (prefer orchestrator overall system health)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8090/health')" || exit 1

# Default command
CMD ["python", "-m", "macbot.orchestrator"]
