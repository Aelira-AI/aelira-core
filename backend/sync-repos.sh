#!/bin/bash
# Sync Backend to Both Private and Opensource Repos
# This ensures changes are pushed to both rdcrampton/aelira-project and Aelira-AI/aelira-core

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Syncing backend to both repos...${NC}"

# Push to private repo (origin)
echo "Pushing to private repo (rdcrampton/aelira-project)..."
git push origin main

# Push to opensource repo
echo "Pushing to opensource repo (Aelira-AI/aelira-core)..."
git push opensource main

echo -e "${GREEN}✅ Successfully synced to both repos!${NC}"
