# Build stage
FROM node:20-alpine AS builder
WORKDIR /app

# Update npm to latest
RUN npm install -g npm@11.6.2

# Build arguments for Vite environment variables (must be set at build time)
ARG VITE_API_URL=https://api.aelira.ai
ARG VITE_WEBSITE_URL=https://aelira.ai

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
FROM nginx:alpine
WORKDIR /usr/share/nginx/html

# Copy build artifacts from builder
COPY --from=builder /app/dist .

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
