#!/bin/bash
# Build and run the Docker container with the bot

# Check that .env exists
if [ ! -f .env ]; then
  echo ".env not found. Creating from the example..."
  cp example.env .env
  echo "Please edit .env and set BOT_TOKEN"
  exit 1
fi

# Check that BOT_TOKEN is set in .env
if ! grep -q "BOT_TOKEN=" .env || grep -q "BOT_TOKEN=$" .env || grep -q "BOT_TOKEN=your_token_here" .env; then
  echo "BOT_TOKEN is not set in .env"
  echo "Please edit .env and set BOT_TOKEN"
  exit 1
fi

# Build the Docker image
echo "Building the Docker image..."
docker build -t telegram-helper:latest .

# Start the container via Docker Compose
echo "Starting the container via Docker Compose..."
docker compose down
docker compose up -d

# Show logs
echo "Container started. Showing logs (Ctrl+C to exit):"
docker compose logs -f | cat
