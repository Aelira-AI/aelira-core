#!/bin/bash
# Cleanup Old AI Models - Free 5.4GB Disk Space
# Run ONLY after verifying new models (Qwen 0.5B, Qwen Coder 1.5B) work properly

set -e

echo "🧹 Cleaning up old AI models..."
echo ""
echo "⚠️  WARNING: This will remove:"
echo "  • Llama 3.2 3B (~2.0 GB)"
echo "  • Qwen 2.5 Coder 7B (~4.7 GB)"
echo "  • Total space freed: ~5.4 GB"
echo ""
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    echo "Cancelled."
    exit 1
fi

echo ""
echo "📊 Disk space BEFORE cleanup:"
df -h / | grep -E '^Filesystem|^/dev/'

echo ""
echo "🗑️  Removing old models..."

# Remove Llama 3.2 3B (content analysis - replaced with Qwen 0.5B)
echo "  Removing llama3.2:3b..."
docker exec aelira-backend-ollama ollama rm llama3.2:3b 2>/dev/null || echo "    (already removed)"

echo "  Removing llama3.2:latest..."
docker exec aelira-backend-ollama ollama rm llama3.2:latest 2>/dev/null || echo "    (already removed)"

# Remove Qwen 2.5 Coder 7B (code fixes - replaced with 1.5B)
echo "  Removing qwen2.5-coder:7b..."
docker exec aelira-backend-ollama ollama rm qwen2.5-coder:7b 2>/dev/null || echo "    (already removed)"

echo ""
echo "✅ Old models removed!"

echo ""
echo "📊 Disk space AFTER cleanup:"
df -h / | grep -E '^Filesystem|^/dev/'

echo ""
echo "📋 Remaining models:"
docker exec aelira-backend-ollama ollama list

echo ""
echo "🎉 Cleanup complete! Freed approximately 5.4GB."
