#!/bin/bash
#
# Aelira Backend - Development Setup Script
# Sets up local Docker environment with Ollama + AI models
#

set -e  # Exit on error

echo "🚀 Aelira Backend - Local Development Setup"
echo "==========================================="
echo ""

# Step 1: Start Docker services
echo "📦 Step 1: Starting Docker services (Ollama, PostgreSQL, Redis)..."
docker-compose -f docker-compose.dev.yml up -d

echo "⏳ Waiting for services to be healthy (30s)..."
sleep 30

# Step 2: Check Ollama is running
echo ""
echo "🤖 Step 2: Checking Ollama service..."
if ! docker exec aelira-ollama-dev ollama list >/dev/null 2>&1; then
    echo "❌ Ollama not responding. Waiting another 15s..."
    sleep 15
fi

# Step 3: Pull AI models
echo ""
echo "📥 Step 3: Pulling AI models (this may take 10-15 minutes)..."
echo ""
echo "  Pulling Llama 3.2 3B (fast classification)..."
docker exec aelira-ollama-dev ollama pull llama3.2:3b

echo ""
echo "  Pulling Qwen 2.5 Coder 7B (code generation)..."
docker exec aelira-ollama-dev ollama pull qwen2.5-coder:7b

# Step 4: Verify models
echo ""
echo "✅ Step 4: Verifying models..."
docker exec aelira-ollama-dev ollama list

# Step 5: Test API
echo ""
echo "🧪 Step 5: Testing API endpoints..."
sleep 5  # Give API time to start

echo "  Testing health endpoint..."
curl -s http://localhost:8000/health | python3 -m json.tool || echo "API not ready yet"

echo ""
echo "  Testing AI health endpoint..."
curl -s http://localhost:8000/api/ai/health | python3 -m json.tool || echo "AI not ready yet"

echo ""
echo "✨ Setup complete!"
echo ""
echo "📝 Next steps:"
echo "  1. Visit http://localhost:8000/docs for API documentation"
echo "  2. Test AI analysis: curl http://localhost:8000/api/test-ai"
echo "  3. View logs: docker-compose -f docker-compose.dev.yml logs -f api"
echo ""
echo "🛑 To stop: docker-compose -f docker-compose.dev.yml down"
echo ""
