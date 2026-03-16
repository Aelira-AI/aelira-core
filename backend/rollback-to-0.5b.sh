#!/bin/bash
# Rollback to lighter qwen2:0.5b model for faster AI code generation
# The 7B model is too slow on current hardware (8-36s per fix)
# 0.5b model is much faster (~1-3s per fix) with acceptable quality

echo "Rolling back to qwen2:0.5b for code generation..."

# Update web_scanner.py to use lighter model
sed -i.bak 's/self\.coder_model = "qwen2\.5-coder:7b"/self.coder_model = "qwen2:0.5b"/' src/education/web_scanner.py

echo "✅ Updated web_scanner.py:"
echo "   OLD: qwen2.5-coder:7b (high quality, slow: 8-36s per fix)"
echo "   NEW: qwen2:0.5b (good quality, fast: 1-3s per fix)"

echo ""
echo "Restart the API container to apply changes:"
echo "  docker-compose -f docker-compose.dev.yml restart api"
echo ""
echo "To rollback to 7B model (for production):"
echo "  mv src/education/web_scanner.py.bak src/education/web_scanner.py"
