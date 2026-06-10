# Stage 1: Build Next.js frontend (standalone mode)
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
ENV API_URL=http://localhost:8000
RUN npm run build

# Stage 2: Python backend + Node.js frontend
FROM python:3.13-slim
WORKDIR /app

# Install Node.js for Next.js standalone server
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model
RUN python -m spacy download en_core_web_lg

# Copy backend code
COPY src/ src/
COPY scripts/ scripts/

# Copy frontend standalone build
COPY --from=frontend-build /app/frontend/.next/standalone ./frontend/
COPY --from=frontend-build /app/frontend/.next/static ./frontend/.next/static/
COPY --from=frontend-build /app/frontend/public ./frontend/public/

# Ensure data dirs exist
RUN mkdir -p data/generated data/sample_books

# Start script: run both FastAPI and Next.js
COPY start.sh .
RUN chmod +x start.sh

ENV PORT=8080
EXPOSE 8080

CMD ["./start.sh"]
