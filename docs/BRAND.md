# Nova — Brand Guidelines

## 📋 Overview

**Nova** is an automated research ideation platform that transforms research directions into publication-ready proposals in minutes, not weeks. Built on a 12-phase AI pipeline, Nova searches literature, identifies bottlenecks, generates novel candidates, validates novelty, and produces structured proposals with equations.

### Mission
Accelerate scientific discovery by automating the tedious parts of research ideation — from literature review to hypothesis generation — so researchers can focus on experimentation and insight.

### Vision
A world where groundbreaking research is accessible to every researcher, not just those with years of literature review experience.

---

## 🎯 Value Proposition

| Aspect | What It Means |
|--------|---------------|
| **Speed** | From idea direction to proposal in ~30 minutes |
| **Novelty-First** | 7-step novelty check before full pipeline commitment |
| **Field-Agnostic** | Works for biology, CS, sociology, materials science, etc. |
| **Transparent** | Full pipeline visibility; no black-box outputs |
| **Cost-Efficient** | ~$0.33 per full research run |

---

## 🗣️ Tone & Voice

| Principle | Description | Example |
|-----------|-------------|---------|
| **Precise, not verbose** | Use clear technical terms; avoid fluff | "12-phase AI pipeline" not "cutting-edge multi-step automated system" |
| **Confident, not arrogant** | Make claims backed by metrics | "~30 min end-to-end" not "instant results" |
| **Accessible, not simplistic** | Assume intelligence; explain jargon | "falsification predictions" → "tests your hypothesis against known counter-examples" |
| **Optimistic, not hyperbolic** | Focus on enabling; not replacing researchers | "augment your ideation" not "replace the literature review" |

---

## 🎨 Visual Identity

### Colors

| Element | Value | Usage |
|---------|-------|-------|
| **Background** | `#08090a` | Main app background |
| **Surface** | `#121212` | Cards, panels |
| **Primary Accent** | `#4f46e5` (indigo) | Buttons, active states |
| **Secondary Accent** | `#06b6d4` (cyan) | Progress, highlights |
| **Text Primary** | `#f9fafb` | Headings, labels |
| **Text Secondary** | `#9ca3af` | Body text, metadata |

### Typography

| Element | Font | Weight |
|---------|------|--------|
| **Headings** | Inter | 600-700 |
| **Body** | Inter | 400 |
| **Code/Math** | Fira Code / KaTeX | 400 |

### Iconography

- **Nova Logo**: Simple star/supernova glyph (⚡ or ★)
- **Features**: Emoji-based icons for quick recognition (🔍, ⚡, 📚, ✅)

---

## 🏷️ Naming Conventions

| Context | Usage |
|---------|-------|
| **Product** | `Nova` or `Nova Labs` |
| **Feature (Novelty Check)** | `Scoop-Check` |
| **Feature (Full Pipeline)** | `IdeaSpark` or `Nova Pipeline` |
| **Documentation** | `docs/BRAND.md`, `README.md` |
| **Scripts** | `run_nova.sh`, `deploy/nova.service` |

---

## 📐 UI/UX Principles

1. **Dark Mode First**: All UI designed for dark theme (eye comfort for long sessions)
2. **Horizontal Stepper**: Visual progress through 12 phases
3. **Expandable Details**: Phase cards collapse by default; details on click
4. **Real-time Streaming**: Show LLM reasoning as it happens
5. **KaTeX Math**: All equations render beautifully

---

## 🚫 What Nova Is NOT

- ❌ **Not a paper generator** (it generates *ideas* and *proposals*, not final manuscripts)
- ❌ **Not a replacement for researchers** (it's a research assistant, not an autonomous author)
- ❌ **Not a free-form LLM chat** (structured pipeline with validation at each phase)
- ❌ **Not field-specific** (unlike bioinformatics tools, Nova is field-agnostic)

---

## ✅ Brand Checklist (Pre-Release)

- [ ] All references use "Nova" (not IdeaFlow or Open Research)
- [ ] Logo and favicon consistent across pages
- [ ] Dark mode theme applied to all UI components
- [ ] Tone of voice follows guidelines
- [ ] Value proposition is clear on landing page
- [ ] Billing/subscription terms transparent
- [ ] Documentation complete (`docs/BRAND.md`, `README.md`)

---

*Last updated: 2024-09-16*

---

## 💰 Pricing Model

### Self-Hosted (FREE)
Users clone the repository and run with their own OpenRouter API key. No costs from Nova.

### Hosted Service
We route requests through our discounted OpenRouter enterprise account with a **30% convenience margin** covering:
- Server costs (hosting, scaling, uptime)
- Support & maintenance
- API rate limit management

**Pricing:**
| Tier | Runs | Price |
|------|------|-------|
| Free | 10/mo | $0 |
| Pro | Unlimited | $29/mo |

