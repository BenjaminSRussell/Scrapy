#!/bin/bash
# Build and start all services in the scraping pipeline

# Source profile to ensure Docker is in PATH
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc"
elif [ -f "$HOME/.bash_profile" ]; then
    source "$HOME/.bash_profile"
elif [ -f "$HOME/.profile" ]; then
    source "$HOME/.profile"
fi

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not available in PATH"
    echo "Please ensure Docker Desktop is running and try again"
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &> /dev/null; then
    echo "Error: Docker daemon is not running"
    echo "Please start Docker Desktop and try again"
    exit 1
fi

echo "Building and starting all services..."
docker compose up --build -d || {
    echo "Error: Failed to build and start services"
    exit 1
}

echo "Waiting for services to be ready..."
sleep 10

echo "Loading seed URLs from uconn_urls.csv..."
docker compose exec -T scrapy-app python scripts/load_seeds.py || {
    echo "Warning: Failed to load seed URLs, but continuing..."
}

echo "Attaching to logs..."
docker compose logs -f