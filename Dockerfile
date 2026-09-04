FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy dependency spec first for better build caching
COPY requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone \
    && rm -rf /var/lib/apt/lists/*
# Prefer IPv4 for outbound APIs. Some VPS hosts expose a broken IPv6 route in
# host-network mode, which can make Telegram getMe/connect hang on startup.
RUN printf '\nprecedence ::ffff:0:0/96  100\n' >> /etc/gai.conf
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check -r requirements.txt

# Copy application code
COPY . /app

# Create a non-root user and grant permissions to app directory
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# No ports are exposed because Telegram bot uses outbound connections only

# Default command
CMD ["python", "main.py"]
