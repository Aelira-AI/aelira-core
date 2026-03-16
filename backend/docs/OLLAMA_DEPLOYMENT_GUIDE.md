# Ollama Deployment Guide - Sydney VPS (32GB)

**Server:** Sydney VPS (32GB RAM, $150/mo)
**Architecture:** Hybrid (Llama 3.2 3B + Qwen 2.5 Coder 7B)
**Total RAM:** ~12GB for models + 20GB for everything else

---

## Why Hybrid for MVP?

With 32GB RAM, we can run the **optimal architecture from day 1**:

- **Llama 3.2 3B:** Quick classification/summaries (<1s, 4GB RAM)
- **Qwen 2.5 Coder 7B:** Code fixes (2-3s, 8GB RAM)
- **Total:** 3-4s per scan, best quality-to-speed ratio
- **Overhead:** 20GB RAM free for FastAPI, Playwright, PostgreSQL, Redis

---

## Step 1: Install Ollama on Sydney VPS

### SSH into VPS
```bash
ssh user@sydney-vps-ip
```

### Install Ollama (One-line install)
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### Verify Installation
```bash
ollama --version
# Should show: ollama version 0.x.x
```

### Start Ollama Service
```bash
sudo systemctl start ollama
sudo systemctl enable ollama  # Auto-start on boot
sudo systemctl status ollama  # Check it's running
```

---

## Step 2: Pull Models

### Pull Llama 3.2 3B (Fast classification)
```bash
ollama pull llama3.2:3b
```
**Size:** ~2GB download, ~4GB RAM when loaded
**Time:** 2-5 minutes depending on connection

### Pull Qwen 2.5 Coder 7B (Code generation)
```bash
ollama pull qwen2.5-coder:7b
```
**Size:** ~4.5GB download, ~8GB RAM when loaded
**Time:** 5-10 minutes

### Verify Models
```bash
ollama list
```
Should show:
```
NAME                    ID              SIZE    MODIFIED
llama3.2:3b            abc123...       2.0 GB  2 minutes ago
qwen2.5-coder:7b       def456...       4.5 GB  5 minutes ago
```

---

## Step 3: Test Models Locally

### Test Llama 3.2 3B (Classification)
```bash
ollama run llama3.2:3b "Classify this accessibility issue: Missing alt text on image. Is this Critical, High, Medium, or Low severity?"
```

Expected output (should respond in <1s):
```
This is a HIGH severity issue. Missing alt text prevents screen reader 
users from understanding image content, violating WCAG 2.1 Level A 
requirements. It's a common ADA lawsuit trigger.
```

### Test Qwen 2.5 Coder 7B (Code generation)
```bash
ollama run qwen2.5-coder:7b "Generate HTML fix for missing alt text: <img src='logo.png'>"
```

Expected output (should respond in 2-3s):
```html
<!-- Fixed HTML with descriptive alt text -->
<img src="logo.png" alt="Company logo - Aelira ADA Compliance Scanner">

<!-- Always use descriptive alt text that conveys the image's purpose -->
```

### Benchmark Speed
```bash
time ollama run llama3.2:3b "Hello"
time ollama run qwen2.5-coder:7b "Hello"
```

Expected times:
- Llama 3.2 3B: 0.5-1s
- Qwen 2.5 Coder 7B: 2-3s

---

## Step 4: Configure Ollama API

### Set Ollama to Listen on All Interfaces
By default, Ollama only listens on localhost. We need it accessible from FastAPI.

```bash
sudo nano /etc/systemd/system/ollama.service
```

Add environment variable:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Save and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Test API Access
```bash
curl http://localhost:11434/api/tags
```

Should return JSON with installed models.

---

## Step 5: Backend Integration

### Install Python Ollama Client
```bash
cd /path/to/backend
pip install ollama
```

### Create Ollama Client (`src/ai/ollama_client.py`)

```python
import ollama
from typing import Dict, Any, Literal
import json
import time

class OllamaClient:
    """Ollama client for accessibility analysis."""
    
    def __init__(self, host: str = "http://localhost:11434"):
        self.host = host
        self.classifier_model = "llama3.2:3b"
        self.coder_model = "qwen2.5-coder:7b"
    
    async def classify_issue(
        self, 
        rule_id: str,
        impact: str,
        html_snippet: str,
        selector: str
    ) -> Dict[str, Any]:
        """Classify accessibility issue severity using Llama 3.2 3B."""
        
        prompt = f"""You are an accessibility expert analyzing WCAG 2.1 AA violations.

Violation Details:
- Rule: {rule_id}
- Impact: {impact}
- HTML: {html_snippet}
- Selector: {selector}

Classify this issue's severity (Critical/High/Medium/Low) considering:
1. Legal risk (lawsuit potential)
2. User impact (how many users affected)
3. Fix difficulty (how hard to remediate)

Respond ONLY with valid JSON in this exact format:
{{
  "severity": "Critical|High|Medium|Low",
  "explanation": "2 sentence plain-English explanation",
  "business_impact": "1 sentence about business/legal risk"
}}"""

        start = time.time()
        
        response = ollama.chat(
            model=self.classifier_model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "temperature": 0.3,  # Low temp for consistent classifications
                "num_predict": 200,  # Limit response length
            }
        )
        
        elapsed = time.time() - start
        
        # Parse JSON response
        content = response['message']['content']
        try:
            result = json.loads(content)
            result['inference_time'] = elapsed
            return result
        except json.JSONDecodeError:
            # Fallback if model doesn't return valid JSON
            return {
                "severity": impact.capitalize(),  # Use Axe's impact as fallback
                "explanation": content[:200],
                "business_impact": "Requires manual review",
                "inference_time": elapsed
            }
    
    async def generate_fix(
        self,
        rule_id: str,
        violation_description: str,
        html_snippet: str,
        wcag_criterion: str
    ) -> Dict[str, Any]:
        """Generate code fix using Qwen 2.5 Coder 7B."""
        
        prompt = f"""You are an expert web developer specializing in accessibility.

Violation: {violation_description}
Current HTML: {html_snippet}
WCAG Rule: {rule_id} ({wcag_criterion})

Generate a complete fix including:
1. Updated HTML with proper ARIA/semantic tags
2. CSS if needed for visual accessibility
3. JavaScript if needed for keyboard navigation
4. Step-by-step instructions for developers

Be specific - provide copy-paste code, not generic advice.

Respond in this format:
## Fixed HTML
[code]

## Additional CSS (if needed)
[code]

## Additional JavaScript (if needed)
[code]

## Implementation Steps
1. [step]
2. [step]
3. [step]"""

        start = time.time()
        
        response = ollama.chat(
            model=self.coder_model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "temperature": 0.5,  # Moderate temp for creative but accurate fixes
                "num_predict": 1000,  # Allow longer code responses
            }
        )
        
        elapsed = time.time() - start
        
        return {
            "fix_recommendation": response['message']['content'],
            "model": self.coder_model,
            "inference_time": elapsed
        }
    
    async def summarize_report(
        self,
        critical_count: int,
        high_count: int,
        medium_count: int,
        low_count: int,
        total_issues: int,
        top_issues: list[str]
    ) -> Dict[str, Any]:
        """Generate executive summary using Llama 3.2 3B."""
        
        prompt = f"""You are a compliance consultant explaining technical issues to business owners.

Scan Results:
- Critical: {critical_count}
- High: {high_count}
- Medium: {medium_count}
- Low: {low_count}
- Total: {total_issues}

Top Issues:
{chr(10).join(f'- {issue}' for issue in top_issues[:5])}

Summarize in 3 short paragraphs:
1. Overall compliance status (pass/fail, biggest risks)
2. Top 3 priorities to fix immediately
3. Recommended next steps

Use business language, not technical jargon. Focus on lawsuit risk."""

        start = time.time()
        
        response = ollama.chat(
            model=self.classifier_model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "temperature": 0.7,  # Higher temp for natural writing
                "num_predict": 400,
            }
        )
        
        elapsed = time.time() - start
        
        return {
            "summary": response['message']['content'],
            "inference_time": elapsed
        }
    
    def health_check(self) -> Dict[str, Any]:
        """Check if both models are available."""
        try:
            models = ollama.list()
            available_models = [m['name'] for m in models['models']]
            
            return {
                "status": "healthy",
                "classifier_available": self.classifier_model in available_models,
                "coder_available": self.coder_model in available_models,
                "total_models": len(available_models)
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }
```

### Add to FastAPI (`src/api/main.py`)

```python
from src.ai.ollama_client import OllamaClient

# Initialize Ollama client
ollama_client = OllamaClient(host="http://localhost:11434")

@app.get("/api/ai/health")
async def ai_health():
    """Check AI models status."""
    return ollama_client.health_check()

@app.post("/api/analyze")
async def analyze_violation(
    rule_id: str,
    impact: str,
    html: str,
    selector: str
):
    """Analyze accessibility violation with AI."""
    classification = await ollama_client.classify_issue(
        rule_id=rule_id,
        impact=impact,
        html_snippet=html,
        selector=selector
    )
    
    # Only generate fix for High/Critical issues
    if classification['severity'] in ['Critical', 'High']:
        fix = await ollama_client.generate_fix(
            rule_id=rule_id,
            violation_description=classification['explanation'],
            html_snippet=html,
            wcag_criterion=rule_id
        )
        classification['fix'] = fix
    
    return classification
```

---

## Step 6: Performance Optimization

### Keep Models Loaded (Reduce Cold Start)
```bash
# Pre-load models on server boot
ollama run llama3.2:3b ""  # Loads model into RAM
ollama run qwen2.5-coder:7b ""  # Loads model into RAM
```

Add to systemd service or cron job:
```bash
sudo crontab -e
# Add:
@reboot sleep 60 && ollama run llama3.2:3b "" && ollama run qwen2.5-coder:7b ""
```

### Monitor RAM Usage
```bash
# Watch RAM while models are running
watch -n 1 free -h
```

Expected:
- Idle: ~2GB used
- Llama 3.2 3B loaded: ~6GB used
- Both loaded: ~14GB used
- With FastAPI/Playwright/PostgreSQL: ~18-20GB used
- **Free:** ~12GB buffer

---

## Step 7: Docker Compose Integration

### Update `docker-compose.yml`

```yaml
version: '3.8'

services:
  # Ollama service
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 16G  # Reserve 16GB for models
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 30s
      timeout: 10s
      retries: 3
  
  # FastAPI backend
  api:
    build: .
    container_name: aelira-api
    ports:
      - "8000:8000"
    environment:
      - OLLAMA_HOST=http://ollama:11434
    depends_on:
      - ollama
      - postgres
    restart: unless-stopped
  
  # PostgreSQL
  postgres:
    image: postgres:16
    container_name: postgres
    environment:
      POSTGRES_DB: aelira
      POSTGRES_USER: aelira
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  ollama_data:
  postgres_data:
```

---

## Step 8: Production Deployment

### Start Services
```bash
docker-compose up -d
```

### Pull Models in Container
```bash
docker exec ollama ollama pull llama3.2:3b
docker exec ollama ollama pull qwen2.5-coder:7b
```

### Verify
```bash
curl http://localhost:8000/api/ai/health
```

Should return:
```json
{
  "status": "healthy",
  "classifier_available": true,
  "coder_available": true,
  "total_models": 2
}
```

---

## Expected Performance (32GB VPS)

### RAM Allocation
- Ollama (both models): ~12GB
- FastAPI + Workers: ~2GB
- Playwright (headless browsers): ~4GB
- PostgreSQL: ~2GB
- Redis: ~1GB
- System overhead: ~3GB
- **Free buffer:** ~8GB

### Scan Performance
1. Axe-core scan: 60 seconds (Playwright)
2. AI classification (Llama 3B): <1s per violation
3. AI fix generation (Qwen 7B): 2-3s for High/Critical only
4. **Total per scan:** 60-90 seconds (depending on violation count)

### Throughput
- **Sequential:** ~1 scan/minute
- **Parallel (4 workers):** ~4 scans/minute
- **Daily capacity:** ~5,760 scans/day (at 1/min)

---

## Monitoring & Debugging

### Check Ollama Logs
```bash
sudo journalctl -u ollama -f
```

### Monitor Model Performance
```bash
# Install nvidia-smi equivalent for CPU monitoring
htop

# Watch specific Ollama process
ps aux | grep ollama
```

### Test Individual Models
```bash
# Quick classification test
time curl -X POST http://localhost:11434/api/generate \
  -d '{"model":"llama3.2:3b", "prompt":"Classify: missing alt text", "stream":false}'

# Quick code generation test
time curl -X POST http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5-coder:7b", "prompt":"Fix: <img src=logo.png>", "stream":false}'
```

---

## Troubleshooting

### Model Not Loading
```bash
# Clear Ollama cache
sudo rm -rf ~/.ollama/models
ollama pull llama3.2:3b
```

### Slow Inference
```bash
# Check CPU/RAM usage
top

# Ensure models are using CPU efficiently
# (No GPU on VPS, so should max out CPU cores)
```

### Out of Memory
```bash
# Reduce model concurrency
# Edit docker-compose.yml:
deploy:
  resources:
    limits:
      memory: 12G  # Reduce to 12GB if needed
```

---

## Cost Breakdown

**Sydney VPS:** $150/mo (32GB RAM)
- Ollama models: $0/mo (open-source)
- Per-scan cost: $0 🎉
- **Total:** $150/mo for unlimited scans

**vs OpenAI API:**
- GPT-4 Turbo: $0.05/scan
- At 10K scans/mo: $500/mo
- **Savings:** $350/mo with Ollama

---

## Next Steps

1. SSH into Sydney VPS
2. Run Ollama installation commands
3. Pull both models (Llama 3.2 3B + Qwen 2.5 Coder 7B)
4. Test locally with curl commands
5. Deploy backend with Docker Compose
6. Run first test scan!

**Ready to deploy?** 🚀
