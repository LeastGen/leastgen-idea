# LeastGen Hosting Guide

## Two Ways to Use LeastGen

### 1. Self-Hosted (FREE)
Clone the repository and run it yourself with your own API keys:

```bash
git clone https://github.com/KhalidAlnujaidi/leastgen
cd leastgen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo 'OPENROUTER_API_KEY=your-key' >> .env
uvicorn backend.main:app --host 0.0.0.0 --port 8756
```

**Cost:** Free (you pay OpenRouter directly for token usage)

### 2. LeastGen Hosted Service
If you prefer not to manage servers, we offer a hosted version:

- **Free Tier:** 10 runs/month
- **Pro Monthly:** $29/mo (unlimited runs)
- **Pro Yearly:** $290/yr (unlimited runs, 2 months free)

**How it works:**
- We route your requests through our discounted OpenRouter enterprise account
- A 30% convenience margin covers server costs, support, and maintenance
- No setup, scaling, or key management required

## API Access

Both self-hosted and hosted versions share the same API:

```bash
# Self-hosted (localhost)
curl http://localhost:8756/api/pipeline/start \
  -H "Content-Type: application/json" \
  -d '{"query": "efficient LLM inference"}'

# Hosted service
curl https://api.leastgen.com/api/pipeline/start \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "efficient LLM inference"}'
```

## Pricing Notes

- Self-hosted: You pay OpenRouter directly based on token usage
- Hosted: Fixed monthly pricing includes all token costs + convenience margin
