#!/bin/bash
# Ollama Model Setup Script for Aelira Production
# Run this on the production server: bash setup_ollama_models.sh

set -e

echo "========================================"
echo "Aelira Ollama Model Setup"
echo "========================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Detect Ollama container name
echo "1. Detecting Ollama container..."
OLLAMA_CONTAINER=$(docker ps --filter "name=ollama" --format "{{.Names}}" | head -n 1)

if [ -z "$OLLAMA_CONTAINER" ]; then
    echo -e "${RED}✗ Ollama container is not running${NC}"
    echo "Starting Ollama container..."
    cd /opt/aelira-backend
    docker-compose -f docker-compose.traefik.yml up -d ollama
    sleep 5
    OLLAMA_CONTAINER=$(docker ps --filter "name=ollama" --format "{{.Names}}" | head -n 1)
fi

if [ -z "$OLLAMA_CONTAINER" ]; then
    echo -e "${RED}✗ Failed to start Ollama container${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Ollama container found: $OLLAMA_CONTAINER${NC}"
echo ""

# Check disk space
echo "2. Checking available disk space..."
available_space=$(df -h / | awk 'NR==2 {print $4}')
echo "   Available space: $available_space"
echo ""

# List current models
echo "3. Currently installed Ollama models:"
docker exec $OLLAMA_CONTAINER ollama list || echo "   No models installed yet"
echo ""

# Prompt for model choice
echo "4. Choose model configuration:"
echo "   [1] Full (qwen2.5:3b + qwen2.5-coder:1.5b) - Recommended, ~2.8GB"
echo "   [2] Balanced (qwen2.5:1.5b + qwen2.5-coder:1.5b) - Good balance, ~1.9GB"
echo "   [3] Minimal (qwen2.5-coder:0.5b only - ALREADY INSTALLED) - ~400MB"
echo ""
read -p "Enter choice [1-3] (default: 1): " choice
choice=${choice:-1}
echo ""

# Pull models based on choice
echo "5. Pulling Ollama models..."
case $choice in
    1)
        echo -e "${YELLOW}→ Pulling qwen2.5:3b (1.9GB)...${NC}"
        docker exec $OLLAMA_CONTAINER ollama pull qwen2.5:3b
        echo -e "${YELLOW}→ Pulling qwen2.5-coder:1.5b (900MB)...${NC}"
        docker exec $OLLAMA_CONTAINER ollama pull qwen2.5-coder:1.5b
        llm_model="qwen2.5:3b"
        coder_model="qwen2.5-coder:1.5b"
        ;;
    2)
        echo -e "${YELLOW}→ Pulling qwen2.5:1.5b (1GB)...${NC}"
        docker exec $OLLAMA_CONTAINER ollama pull qwen2.5:1.5b
        echo -e "${YELLOW}→ Pulling qwen2.5-coder:1.5b (900MB)...${NC}"
        docker exec $OLLAMA_CONTAINER ollama pull qwen2.5-coder:1.5b
        llm_model="qwen2.5:1.5b"
        coder_model="qwen2.5-coder:1.5b"
        ;;
    3)
        echo -e "${GREEN}✓ Using already installed qwen2.5-coder:0.5b${NC}"
        llm_model="qwen2.5-coder:0.5b"
        coder_model="qwen2.5-coder:0.5b"
        ;;
    *)
        echo -e "${RED}Invalid choice, exiting${NC}"
        exit 1
        ;;
esac
echo ""

# Verify models
echo "6. Verifying installed models..."
docker exec $OLLAMA_CONTAINER ollama list
echo ""

# Test model
echo "7. Testing model..."
echo -e "${YELLOW}→ Running quick test: '$llm_model'${NC}"
docker exec $OLLAMA_CONTAINER ollama run $llm_model "Say 'Hello from Aelira!'" --verbose=false
echo ""

# Configuration info
echo "========================================"
echo -e "${GREEN}✓ Ollama setup complete!${NC}"
echo "========================================"
echo ""
echo "Models installed:"
echo "  - LLM model:   $llm_model"
echo "  - Coder model: $coder_model"
echo ""
echo "These models are configured in:"
echo "  backend/src/education/web_scanner.py"
echo ""

if [ "$llm_model" != "qwen2:3b" ] || [ "$coder_model" != "qwen2.5-coder:1.5b" ]; then
    echo -e "${YELLOW}⚠ Note: You chose non-default models${NC}"
    echo "Update src/education/web_scanner.py:"
    echo ""
    echo "    self.llm_model = \"$llm_model\""
    echo "    self.coder_model = \"$coder_model\""
    echo ""
    echo "Then rebuild and restart:"
    echo "    cd /opt/aelira-backend"
    echo "    docker-compose -f docker-compose.traefik.yml build api"
    echo "    docker-compose -f docker-compose.traefik.yml up -d api"
    echo ""
fi

echo "To test web scanning with AI fixes, try scanning a website"
echo "from the dashboard at https://dashboard.aelira.ai"
echo ""
echo "Done! 🎉"

