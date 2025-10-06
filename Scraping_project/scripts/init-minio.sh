#!/bin/bash
# ==================================================================
# MinIO Initialization Script
# ==================================================================
# This script initializes MinIO with required buckets for Delta Lake
# Run this after MinIO is up and running
# ==================================================================

set -e

# Configuration
MINIO_HOST="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${MINIO_ROOT_USER:-minioadmin}"
MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD:-minioadmin123}"
BUCKET_NAME="delta-lake"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}MinIO Initialization Script${NC}"
echo -e "${BLUE}===================================================================${NC}"

# Check if mc (MinIO Client) is installed
if ! command -v mc &> /dev/null; then
    echo -e "${RED}Error: MinIO Client (mc) is not installed${NC}"
    echo "Installing MinIO Client..."

    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install minio/stable/mc
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -o /tmp/mc https://dl.min.io/client/mc/release/linux-amd64/mc
        chmod +x /tmp/mc
        sudo mv /tmp/mc /usr/local/bin/mc
    else
        echo -e "${RED}Unsupported OS. Please install MinIO Client manually: https://min.io/docs/minio/linux/reference/minio-mc.html${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✓ MinIO Client is installed${NC}"

# Configure MinIO alias
echo "Configuring MinIO alias..."
mc alias set local $MINIO_HOST $MINIO_ACCESS_KEY $MINIO_SECRET_KEY

echo -e "${GREEN}✓ MinIO alias configured${NC}"

# Create bucket if it doesn't exist
echo "Checking if bucket '$BUCKET_NAME' exists..."
if mc ls local/$BUCKET_NAME &> /dev/null; then
    echo -e "${GREEN}✓ Bucket '$BUCKET_NAME' already exists${NC}"
else
    echo "Creating bucket '$BUCKET_NAME'..."
    mc mb local/$BUCKET_NAME
    echo -e "${GREEN}✓ Bucket '$BUCKET_NAME' created successfully${NC}"
fi

# Set bucket versioning (required for Delta Lake)
echo "Enabling versioning for bucket '$BUCKET_NAME'..."
mc version enable local/$BUCKET_NAME
echo -e "${GREEN}✓ Versioning enabled${NC}"

# Set bucket policy to allow read/write
echo "Setting bucket policy..."
mc anonymous set download local/$BUCKET_NAME
echo -e "${GREEN}✓ Bucket policy configured${NC}"

# Create directory structure for Delta Lake
echo "Creating Delta Lake directory structure..."
mc ls local/$BUCKET_NAME/scraped_data &> /dev/null || echo "scraped_data directory will be created on first write"

echo -e "${GREEN}===================================================================${NC}"
echo -e "${GREEN}MinIO initialization completed successfully!${NC}"
echo -e "${GREEN}===================================================================${NC}"
echo ""
echo "Bucket URL: $MINIO_HOST/$BUCKET_NAME"
echo "Access Key: $MINIO_ACCESS_KEY"
echo "Secret Key: $MINIO_SECRET_KEY"
echo ""
echo "You can access the MinIO Console at: http://localhost:9001"
echo ""
