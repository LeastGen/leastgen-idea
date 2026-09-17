<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://via.placeholder.com/1000x300/08090a/ffffff?text=LeastGen">
  <img alt="LeastGen Labs" src="https://via.placeholder.com/1000x300/08090a/ffffff?text=LeastGen" width="100%">
</picture>

<p align="center">
  <strong>From a research direction to a publication-ready idea card — fully automated.</strong>
  <br>
  <em>A 12-phase AI pipeline that searches the literature, identifies bottlenecks, generates novel candidates, validates novelty, and produces structured research proposals with math notation.</em>
</p>

<p align="center">
  <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-%2308090a?style=for-the-badge" alt="Quick Start"></a>
  <a href="#-pipeline-phases"><img src="https://img.shields.io/badge/Pipeline-%2308090a?style=for-the-badge" alt="Pipeline"></a>
  <a href="#-scoop-check"><img src="https://img.shields.io/badge/Scoop_Check-%2308090a?style=for-the-badge" alt="Scoop-Check"></a>
  <a href="#%EF%B8%8F-architecture"><img src="https://img.shields.io/badge/Architecture-%2308090a?style=for-the-badge" alt="Architecture"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-%2308090a?style=for-the-badge" alt="MIT License"></a>
</p>

<br>

## 📋 Overview

**LeastGen** is an automated research ideation server. Enter a research direction — any direction, in any field — and it produces a complete, structured research proposal with methodology, equations, literature grounding, and falsification predictions.

It works in **two modes**:

| Mode | Description | Time |
|------|-------------|------|
| **Scoop-Check** | Quick novelty verification — enter a problem + claimed novelty, get a 5-level verdict with prior-art hits | 2–5 min |
| **LeastGen Pipeline** | Full 12-phase pipeline — from a research direction to a complete idea card with math, methodology, and falsification | 20–40 min |

**Built on** [Microsoft Research Studio-Idea](https://github.com/microsoft/ResearchStudio/tree/main/ResearchStudio-Idea), an MIT-licensed ideation framework. LeastGen adds a modern web UI, autonomous orchestration, real-time streaming, and a fast novelty pre-check.

<br>

## ✨ Features
- **English-only** — optimized for English research inputs and outputs

- **Field-agnostic** — works for any discipline: biology, linguistics, sociology, materials science, education, computer science
- **Autonomous LLM orchestration** — 7 LLM phases + 6 automated phases, no manual intervention needed
- **Real-time web UI** — dark-themed SPA with horizontal stepper, KaTeX math rendering, expandable phase details
- **Scoop-Check** — 7-step novelty pre-check in 2–5 minutes before committing to a full pipeline
- **Multi-source literature search** — arXiv, Semantic Scholar, OpenAlex, OpenReview
- **Full-text PDF fetching** — automatically retrieves and caches paper PDFs for deep analysis
- **Collision detection** — checks generated candidates against existing literature before committing
- **5-check audit** — evaluates novelty, falsifiability, gap closure, feasibility, and specificity
- **KaTeX-rendered math** — equations in the final idea card render beautifully
- **Self-hosted** — your data, your API key, your server. Run for free with your own OpenRouter key. Or use our Hosted Service for convenience with discounted models and no setup.

<br>

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- [OpenRouter API key](https://openrouter.ai/keys) for self-hosted usage (FREE) or use our Hosted Service
- 4 GB RAM minimum, 8 GB recommended
- Linux (systemd for auto-restart)

### One-command setup

```bash
curl -fsSL https://raw.githubusercontent.com/LeastGen/leastgen-idea/main/deploy/setup.sh | bash
```

### Manual setup

```bash
# Clone the repo
git clone https://github.com/LeastGen/leastgen-idea.git
cd leastgen-idea

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Fetch the ResearchStudio engine (git-ignored, not in the clone)
bash scripts/fetch_engine.sh

# Set your API key
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.kinox/env

# Start the server
uvicorn backend.main:app --host 0.0.0.0 --port 8756
```

Open **http://localhost:8756** in your browser.

### Self-hosting with systemd

```bash
sudo cp deploy/leastgen.service /etc/systemd/system/
sudo systemctl enable leastgen
sudo systemctl start leastgen
```

<br>

## 🔬 Pipeline Phases

The LeastGen Pipeline runs **12 phases** in sequence:

```
Phase 0      Literature search  ─── arXiv, OpenAlex, Semantic Scholar
Phase 0+     Full-text fetch    ─── PDF retrieval and caching
Phase 1      Bottleneck ID      ─── Identify research bottlenecks (LLM)
Phase 2.1    Gap × pattern      ─── Select patterns to address gaps (LLM)
Phase 2.2    Candidate gen      ─── Generate novel idea candidates (LLM)
Phase 2.3    Coherence trace    ─── Algorithm dry-run / coherence check (LLM)
Phase 3.1    Collision check    ─── Prior-art collision detection (automated)
Phase 3.2    5-check audit      ─── Novelty, falsifiability, feasibility (LLM)
Phase 3.3    Revision           ─── Patch flaws found in audit (LLM)
Phase 4      Skeleton → prose   ─── Build idea card with equations (mixed)
```

**Output:** A complete `idea.std.en.md` with:

- Title and motivation
- Methodology with KaTeX equations
- Falsification prediction
- Compute budget
- Related work positioning

### Running the pipeline

```bash
# Start a new pipeline run
./run_pipeline.sh start "efficient speculative decoding for multilingual LLM inference"

# Check status
./run_pipeline.sh status <run-id>

# Watch mode — auto-runs all pending phases
./run_pipeline.sh watch

# List all runs
./run_pipeline.sh list
```

<br>

## 🔍 Scoop-Check

Before committing to a full pipeline, quickly check if your idea overlaps with existing work:

```bash
curl -X POST http://localhost:8756/api/scoop-check/start \
  -H "Content-Type: application/json" \
  -d '{"problem": "efficient LLM inference for long contexts", "novelty": "calibrated per-token early-exit stop rule"}'

# Check status
curl http://localhost:8756/api/scoop-check/<scoop-id>
```

Or use the UI at `http://localhost:8756/api/ui`.

### Scoop-Check steps

| Step | What happens |
|------|-------------|
| 1 | Decompose novelty into 4 axes (problem, mechanism, insight, domain) |
| 2 | Search literature via Phase 0 |
| 3 | Score each paper against the 4 axes |
| 4 | Identify high-potential candidates (overlap ≥ 2/4) |
| 5 | Deep-dive analysis into top 5 candidates |
| 6 | Produce 5-level novelty verdict |
| 7 | Generate summary with recommendations |

<br>

## 🏗️ Architecture

```
leastgen/
├── backend/
│   ├── main.py              # FastAPI application entry point
│   └── routers/
│       ├── ui.py            # Frontend SPA server
│       ├── pipeline.py      # Pipeline orchestration
│       ├── scoop.py         # Scoop-Check API
│       ├── auto.py          # Auto phase trigger
│       ├── phases.py        # Phase execution endpoints
│       ├── results.py       # Result retrieval
│       ├── fulltext.py      # Full-text fetch
│       ├── dashboard.py     # Legacy dashboard
│       └── health.py        # Health check & diagnostics
├── frontend/
│   ├── index.html           # SPA entry point
│   ├── style.css            # Design system (dark theme)
│   └── app.js               # Application logic
├── researchstudio/           # Microsoft ResearchStudio-Idea engine
├── llm_bridge.py             # OpenRouter LLM bridge
├── run_pipeline.sh           # Pipeline orchestrator
├── run_llm_phase.py          # Autonomous LLM phase runner
├── run_scoop.py              # Scoop-Check runner
├── deploy/
│   └── leastgen.service          # systemd service unit
└── LICENSE                   # MIT
```

### Tech stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11+, FastAPI, Uvicorn |
| **Frontend** | Vanilla JS SPA, KaTeX, marked |
| **LLM** | OpenRouter (deepseek/deepseek-v4-flash, 1M context) |
| **Literature** | arXiv (feedparser), Semantic Scholar, OpenAlex |
| **Ideation Engine** | Microsoft Research Studio-Idea (MIT) |
| **Deployment** | systemd, self-hosted |

<br>

## 📊 Benchmark

Single full pipeline run:

| Metric | Value |
|--------|-------|
| **End-to-end time** | ~38 minutes |
| **API cost** | ~$0.33 (deepseek-v4-flash) |
| **LLM calls** | 7 |
| **Automated phases** | 6 |
| **Papers searched** | ~37 across 3 libraries |
| **Collision hits checked** | ~240 |

<br>

## ⚙️ Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | OpenRouter API key | (required) |
| `IDEAS_FLOW_FAST_MODEL` | Fast model for classify tasks | `deepseek/deepseek-v4-flash` |
| `IDEAS_FLOW_LARGE_MODEL` | Large model for reasoning | `deepseek/deepseek-v4-flash` |
| `IDEAS_FLOW_TIMEOUT` | LLM call timeout in seconds | `180` |

Configurable via `~/.kinox/env` or environment variables.

<br>

## 📸 Screenshots

> *The UI is a dark-themed SPA with two modes, a horizontal stepper, and KaTeX-rendered math.*
>
> Visit `http://localhost:8756/api/ui` to see it in action.

<br>

## 🗺️ Roadmap

- [ ] Pattern_summary parallelization (currently 37 sequential LLM calls → batch)
- [ ] Phase 3.2 input truncation (collision_hits.json → top 50 hits)
- [ ] Docker container
- [ ] WebSocket real-time streaming
- [ ] Export to PDF/LaTeX
- [ ] Multi-query batch mode
- [ ] Citation graph visualization

<br>

## 🏠 Open Source & Hosted Service

LeastGen is fully open source (MIT License) — you can clone, modify, and self-host for free. We also offer a hosted service with discounted models and no setup:

| Plan | Runs | Price | Best For |
|------|------|-------|----------|
| **Self-Hosted** | Unlimited | FREE (you pay OpenRouter) | Developers with existing keys |
| **Free (Hosted)** | 10/mo | FREE | Quick testing without server setup |
| **Pro Monthly** | Unlimited | $29/mo | Regular researchers |
| **Pro Yearly** | Unlimited | $290/yr | Teams & long-term users |

**Hosted Service:** We route through our discounted OpenRouter account with a 30% convenience margin covering server costs, support, and maintenance.

See [docs/HOSTING.md](docs/HOSTING.md) for detailed hosting options.

## 🤝 Contributing

Contributions are welcome! Open an issue or PR for:

- New literaturesearch connectors
- Additional ideation patterns
- UI improvements
- Performance optimizations
- Bug fixes

<br>

## 📄 License

MIT — see [LICENSE](LICENSE).

Built on [Microsoft Research Studio-Idea](https://github.com/microsoft/ResearchStudio/tree/main/ResearchStudio-Idea) — the ideation framework behind the pipeline phases. Both projects are MIT-licensed.

<br>

## 🙏 Acknowledgements

- [Microsoft Research Studio-Idea](https://github.com/microsoft/ResearchStudio/tree/main/ResearchStudio-Idea) for the ideation framework
- [OpenRouter](https://openrouter.ai/) for accessible LLM inference
- [arXiv](https://arxiv.org/), [Semantic Scholar](https://www.semanticscholar.org/), and [OpenAlex](https://openalex.org/) for open research literature access

---

<p align="center">
  <sub>Built with ❤️ for open research acceleration.</sub>
</p>