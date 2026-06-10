#!/bin/sh
# Deploy to Cloud Run from source
# Usage: ./deploy.sh

set -e

echo "Pushing to GitHub..."
git push origin main

echo ""
echo "Deploying to Cloud Run..."
gcloud run deploy picture-book-gen \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 600

echo ""
echo "Done! Live at: https://picture-book-gen-264948620024.us-central1.run.app/"
