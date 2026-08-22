# Build stage
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS builder
WORKDIR /app

# Update npm to latest
RUN npm install -g npm@11.6.2

# Build arguments for Vite environment variables (must be set at build time)
ARG VITE_API_URL=https://api.example.com
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
WORKDIR /usr/share/nginx/html

# Copy build artifacts from builder
COPY --from=builder /app/dist .

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
