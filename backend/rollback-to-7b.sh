#!/bin/bash
# Rollback to qwen2.5-coder:7b model (if you have GPU or want highest quality)
# The 3B model is faster and good quality, but 7B is best if you can afford the time

echo "Rolling back to qwen2.5-coder:7b for code generation..."

# Update web_scanner.py to use 7B model
sed -i.bak 's/self\.coder_model = "qwen2\.5-coder:3b"/self.coder_model = "qwen2.5-coder:7b"/' src/education/web_scanner.py
sed -i.bak2 's/# Qwen 2\.5 Coder 3B (5-15 tokens\/sec on CPU, 20-40s per fix, higher quality)/# Qwen 2.5 Coder 7B (1-3 tokens\/sec on CPU, 100-300s per fix, highest quality)/' src/education/web_scanner.py
sed -i.bak3 's/self\.ai_timeout_seconds = 45/self.ai_timeout_seconds = 120/' src/education/web_scanner.py

echo "✅ Updated web_scanner.py:"
echo "   OLD: qwen2.5-coder:3b (good quality, fast: 20-40s per fix)"
echo "   NEW: qwen2.5-coder:7b (best quality, slow: 100-300s per fix)"
echo ""
echo "Restart the API container to apply changes:"
echo "  docker-compose -f docker-compose.dev.yml restart api"
echo ""
echo "To rollback to 3B model (recommended for CPU):"
echo "  mv src/education/web_scanner.py.bak src/education/web_scanner.py"
