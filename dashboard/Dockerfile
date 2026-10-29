# Build stage
FROM node:26-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS builder
WORKDIR /app

# Update npm to latest
RUN npm install -g npm@11.6.2

# Build arguments for Vite environment variables (must be set at build time)
ARG VITE_API_URL=/api
ARG VITE_WEBSITE_URL=https://example.com

# Set environment variables for the build
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_WEBSITE_URL=$VITE_WEBSITE_URL

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine@sha256:db35bfc6b2951e7f8a72db5db120288c127ffaeeb4a6d4b95a26fead017d5913

# Install security fixes published after the pinned base image. apk.log embeds
# wall-clock time, so remove it to keep independent image builds reproducible.
RUN apk upgrade --no-cache libcrypto3 libssl3 libuuid libexpat pcre2 tiff libx11 brotli-libs curl libcurl \
    && rm -f /var/log/apk.log

WORKDIR /usr/share/nginx/html

# Copy build artifacts from builder
COPY --from=builder /app/dist .

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
