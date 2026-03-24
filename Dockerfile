# Use Python 3.12-slim as the base (matches Superset v6 baseline in this repo)
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUPERSET_HOME=/app/superset_home \
    PYTHONPATH=/app/pythonpath \
    SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config.py

WORKDIR /app

# 1. Install System Dependencies (CRITICAL STEP)
# 'build-essential' provides the g++ compiler needed for python-geohash
# 1. Install System Dependencies (CRITICAL STEP)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    zlib1g-dev \
    libjpeg62-turbo-dev \
    libssl-dev \
    libffi-dev \
    libsasl2-dev \
    libldap2-dev \
    default-libmysqlclient-dev \
    libpq-dev \
    pkg-config \
    git \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# 2. Upgrade pip and install wheel
# This helps prevent some legacy build errors
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# 3. Install Superset 6.0.0
# We use the same pipe command pattern as before
RUN curl -sS https://raw.githubusercontent.com/apache/superset/6.0.0/requirements/base.txt | \
    tail -n +10 | \
    awk -v ORS=" " '/^[A-z]/{print}' | \
    xargs pip install "apache-superset==6.0.0"

# 4. Install additional dependencies
RUN pip install --no-cache-dir Authlib flask-cors psycopg2-binary

# 5. Create directories
RUN mkdir -p /app/pythonpath /app/superset_home

# 6. Copy the entrypoint script
# Since the context is already ./superset/superset_project, 
# just use the filename directly.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# 7. Strip Windows CRLF line endings (safety net) and set permissions
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

EXPOSE 9196

ENTRYPOINT ["/app/docker-entrypoint.sh"]
