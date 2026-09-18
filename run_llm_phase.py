#!/usr/bin/env python3
"""
run_llm_phase.py — Run an LLM pipeline phase via OpenRouter (1M context).

Reads the system prompt + input files for a given phase, constructs the
LLM call, and writes the output JSON. Uses deepseek/deepseek-v4-flash
via OpenRouter for full 1M context window.

Usage:
  python3 run_llm_phase.py <run-dir> <phase>
  python3 run_llm_phase.py /path/to/run 1
  python3 run_llm_phase.py /path/to/run 2.1
  python3 run_llm_phase.py /path/to/run 4.fill
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"  # 1M context window
TIMEOUT = 600  # 10 minutes per phase
MAX_RETRIES = 3

SKILL_DIR = Path(__file__).resolve().parent / "researchstudio" / "ResearchStudio-Idea" / "skills" / "idea_spark"
REF_DIR = SKILL_DIR / "references"
PROMPT_DIR = REF_DIR / "system-prompts"
SUB_PATTERNS_DIR = REF_DIR / "ideation-sub-patterns"

# ── Phase configuration ─────────────────────────────────────────────────────
PHASE_CONFIG = {
    "1": {
        "prompt": "bottleneck_identify.txt",
        "output": "phase1/phase1_output.json",
        "inputs": [
            "phase0/lit_table.md",
            "phase0/fulltext_cache.json",
            "phase0/lit_results.json",
            str(REF_DIR / "intake-routing.md"),
        ],
        "required_keys": ["bottleneck_statement", "state", "closest_adjacent"],
        "extra_context": "",
    },
    "2.1": {
        "prompt": "ideate_select.txt",
        "output": "phase2_select/phase2_select_output.json",
        "inputs": [
            "phase1/phase1_output.json",
            str(REF_DIR / "ideation-patterns" / "overview.md"),
            str(REF_DIR / "ideation-patterns" / "companion-combos.md"),
            "phase0/lit_table.md",
        ],
        "required_keys": ["selected_gaps", "coherence_thread_type"],
        "extra_context": "",
    },
    "2.2": {
        "prompt": "ideate_generate.txt",
        "output": "phase2_generate/phase2_generate_output.json",
        "inputs": [
            "phase2_select/phase2_select_output.json",
            str(REF_DIR / "ideation-sub-patterns" / "overview.md"),
            "phase0/lit_results.json",
            "phase0/fulltext_cache.json",
        ],
        "required_keys": ["title", "core_mechanism", "gap_closure"],
        "extra_context": "",
    },
    "2.3": {
        "prompt": "coherence_trace.txt",
        "output": "phase2_coherence/phase2_coherence_output.json",
        "inputs": [
            "phase2_generate/phase2_generate_output.json",
            "phase2_select/phase2_select_output.json",
        ],
        "required_keys": ["trace_report", "verdict"],
        "extra_context": "",
    },
    "3.2": {
        "prompt": "critique.txt",
        "output": "phase3_critique/phase3_critique_output.json",
        "inputs": [
            "phase2_coherence/refined_candidate.json",
            "phase2_select/phase2_select_output.json",
            "phase0/lit_table.md",
            "phase3_collision/collision_hits.json",
            str(REF_DIR / "anti-patterns.md"),
        ],
        "required_keys": ["verdict", "gap_closure_reject_check", "falsification_structure_check"],
        "extra_context": "",
    },
    "3.3": {
        "prompt": "revise.txt",
        "output": "phase3_revise/phase3_revise_output.json",
        "inputs": [
            "phase2_coherence/refined_candidate.json",
            "phase2_select/phase2_select_output.json",
            "phase3_critique/phase3_critique_output.json",
        ],
        "required_keys": ["applied_revisions"],
        "extra_context": "",
    },
    "4.fill": {
        "prompt": "expand.txt",
        "output": "phase4/fill_map.json",
        "inputs": [
            "phase4/phase4_skeleton.json",
        ],
        "required_keys": [],
        "extra_context": "",
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────

# Legacy self-host fallback path for the API key file. The OPENROUTER_API_KEY
# env var takes precedence; this file is only consulted when it is unset.
LEGACY_KEY_FILE = Path("/home/enigma/.kinox/env")


def get_api_key() -> str:
    """Get OpenRouter API key."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    kinox = LEGACY_KEY_FILE
    if kinox.exists():
        for line in kinox.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def read_file_safe(path: Path, max_bytes: int = 300_000) -> str:
    """Read a file, truncating if too large."""
    if not path.exists():
        return f"[FILE NOT FOUND: {path}]"
    size = path.stat().st_size
    if size > max_bytes:
        with open(path) as f:
            content = f.read(max_bytes)
        return content + f"\n\n[... truncated from {size} bytes to {max_bytes} bytes]"
    return path.read_text(encoding="utf-8", errors="replace")


def extract_json(raw: str) -> dict | None:
    """Extract JSON from model response using multiple strategies."""
    raw = raw.strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from ```json ... ``` block
    json_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', raw)
    if json_block:
        try:
            return json.loads(json_block.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: Extract outermost { ... } pair
    brace_start = raw.find('{')
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(raw)):
            if raw[i] == '{':
                depth += 1
            elif raw[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[brace_start:i + 1])
                    except json.JSONDecodeError:
                        pass

    # Strategy 4: Try to find any JSON-like structure
    # Look for patterns like:  {  "key": value  }
    json_pattern = re.search(r'\{[^{}]*\}', raw)
    if json_pattern:
        try:
            return json.loads(json_pattern.group())
        except json.JSONDecodeError:
            pass

    return None


def validate_phase_output(data: dict, phase: str) -> list[str]:
    """Validate that the output has the required keys for this phase."""
    cfg = PHASE_CONFIG.get(phase, {})
    required = cfg.get("required_keys", [])
    missing = [k for k in required if k not in data]
    return missing


def sanitize_revisions(revisions: list[dict], candidate: dict) -> list[dict]:
    """Remove or fix revision ops that would fail on the merger."""
    sanitized = []
    for rev in revisions:
        op = rev.get("op", "")
        field = rev.get("field", "")

        # Skip append_sentence on field paths that point into a string
        if op == "append_sentence" and "[" in field:
            # Check if the parent path is actually a string in the candidate
            parts = field.replace("]", "").split("[")
            parent_key = parts[0]
            if isinstance(candidate.get(parent_key), str):
                print(f"  ⚠ Skipping append_sentence on string field '{field}'")
                continue

        # Normalize op names for the merger
        if op == "swap_sub_pattern":
            rev["op"] = "replace"
            sanitized.append(rev)
        else:
            sanitized.append(rev)
    return sanitized


def call_openrouter(system: str, user: str, phase: str = "",
                    model: str = MODEL, timeout: int = TIMEOUT) -> dict:
    """Call OpenRouter with retry logic and robust JSON extraction."""
    api_key = get_api_key()
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 16384,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/nousresearch/hermes",
        "X-Title": "IdeaFlow-Auto",
    }

    last_error = ""
    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            wait = 2 ** attempt
            print(f"  Retry {attempt}/{MAX_RETRIES} after {wait}s...", flush=True)
            time.sleep(wait)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(OPENROUTER_BASE, data=data, headers=headers, method="POST")

        print(f"  Calling OpenRouter ({model})...", flush=True)
        t0 = time.time()

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
            err_body = e.read().decode("utf-8", errors="replace")
            if e.code in (429, 502, 503):
                print(f"  {last_error}: {err_body[:100]}", flush=True)
                continue
            print(f"ERROR: OpenRouter {last_error}: {err_body[:500]}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            last_error = str(e)
            print(f"  Error: {last_error}", flush=True)
            continue

        elapsed = time.time() - t0
        print(f"  Response in {elapsed:.1f}s", flush=True)

        choices = result.get("choices", [])
        if not choices:
            last_error = "no choices"
            continue

        content = choices[0].get("message", {}).get("content", "")
        reasoning = choices[0].get("message", {}).get("reasoning", "")

        # deepseek models sometimes put content in reasoning field
        if not content and reasoning:
            print(f"  Reasoning field used ({len(reasoning)} chars)", flush=True)
            content = reasoning

        if not content:
            last_error = "empty content"
            continue

        # Try to extract JSON
        parsed = extract_json(content)
        if parsed is None:
            print(f"  WARNING: response not parseable as JSON, saving as raw text", flush=True)
            return {"raw": content}

        # Phase-specific validation
        if phase:
            missing = validate_phase_output(parsed, phase)
            if missing:
                print(f"  WARNING: missing required keys: {missing}", flush=True)
                # Still return the data — downstream can handle partial output

        # For Phase 3.3: sanitize revisions
        if phase == "3.3" and "applied_revisions" in parsed:
            # Read the candidate to check field types
            pass  # sanitization happens in main()

        print(f"  JSON parsed successfully ({len(json.dumps(parsed))} bytes)", flush=True)
        return parsed

    print(f"ERROR: All {MAX_RETRIES} retries failed. Last error: {last_error}", file=sys.stderr)
    sys.exit(1)


def find_sub_pattern_cards(run_dir: Path, phase_config: dict) -> list[str]:
    """Find sub-pattern cards referenced in phase2 files."""
    cards = []
    for src in ["phase2_select/phase2_select_output.json", "phase2_generate/phase2_generate_output.json"]:
        src_path = run_dir / src
        if src_path.exists():
            try:
                data = json.loads(src_path.read_text())
                for gap in data.get("gap_closure", data.get("selected_gaps", [])):
                    sp = gap.get("sub_pattern", "")
                    if sp and sp.startswith("C"):
                        code = sp.split(" ")[0]
                        card_path = SUB_PATTERNS_DIR / f"{code}.md"
                        if card_path.exists():
                            cards.append(str(card_path))
            except (json.JSONDecodeError, KeyError):
                pass
    return list(set(cards))


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    run_dir = Path(sys.argv[1]).resolve()
    phase = sys.argv[2]

    if not run_dir.exists():
        print(f"ERROR: run directory not found: {run_dir}", file=sys.stderr)
        sys.exit(1)

    if phase not in PHASE_CONFIG:
        print(f"ERROR: unknown phase '{phase}'. Valid: {', '.join(sorted(PHASE_CONFIG.keys()))}", file=sys.stderr)
        sys.exit(1)

    if not (SKILL_DIR / "scripts" / "run.py").is_file():
        print("ERROR: ResearchStudio engine not found at researchstudio/.", file=sys.stderr)
        print("Fetch it with: bash scripts/fetch_engine.sh", file=sys.stderr)
        sys.exit(1)

    cfg = PHASE_CONFIG[phase]
    prompt_path = PROMPT_DIR / cfg["prompt"]
    output_path = run_dir / cfg["output"]

    # Idempotent resume: never re-fire the LLM when a complete phase output
    # already exists. A previous interrupted write may leave a partial file —
    # require non-empty valid JSON with the phase's required keys; a `raw`
    # fallback dict counts as complete (model returned unparseable text and
    # downstream was told). Use --force to re-run deliberately.
    if "--force" not in sys.argv and output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and (
                "raw" in existing or not validate_phase_output(existing, phase)
            ):
                print(f"  Skipping Phase {phase}: output already complete ({output_path})")
                print("  Pass --force to re-run deliberately.")
                return
            print(f"  Existing output incomplete "
                  f"(missing {validate_phase_output(existing, phase)}); re-running.")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Existing output unreadable ({e}); re-running.")

    if not prompt_path.exists():
        print(f"ERROR: system prompt not found: {prompt_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Phase {phase} — {cfg['prompt']}")
    print(f"  Run: {run_dir.name}")
    print(f"  Output: {output_path}")
    print(f"{'='*60}\n")

    # ── Read system prompt ──
    system_prompt = prompt_path.read_text(encoding="utf-8")
    print(f"  System prompt: {len(system_prompt)} bytes")

    # ── Read input files ──
    user_message_parts = []
    for input_spec in cfg["inputs"]:
        input_path = Path(input_spec)
        if not input_path.is_absolute():
            input_path = run_dir / input_spec
        content = read_file_safe(input_path)
        user_message_parts.append(f"=== {input_path.name} ({input_path}) ===\n\n{content}")

    if cfg["extra_context"]:
        user_message_parts.append(f"=== Extra context ===\n\n{cfg['extra_context']}")

    # Find and add sub-pattern cards
    card_paths = find_sub_pattern_cards(run_dir, cfg)
    for cp in card_paths:
        content = read_file_safe(Path(cp))
        user_message_parts.append(f"=== Sub-pattern card: {Path(cp).name} ===\n\n{content}")

    user_message = "\n\n---\n\n".join(user_message_parts)
    print(f"  Input files: {len(cfg['inputs'])} + {len(card_paths)} sub-pattern cards")
    print(f"  Total input size: {len(user_message)} bytes")

    # ── Create output directory ──
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Call OpenRouter ──
    result = call_openrouter(system_prompt, user_message, phase=phase)

    # ── Phase-specific post-processing ──
    if phase == "3.3" and "applied_revisions" in result and result["applied_revisions"]:
        # Read the candidate to check field types for append_sentence ops
        candidate_path = run_dir / "phase2_coherence" / "refined_candidate.json"
        if candidate_path.exists():
            try:
                candidate = json.loads(candidate_path.read_text())
                result["applied_revisions"] = sanitize_revisions(result["applied_revisions"], candidate)
                print(f"  Sanitized revisions: {len(result['applied_revisions'])}")
            except json.JSONDecodeError:
                pass

    if phase == "3.3" and "applied_revisions" in result:
        # Auto-merge revisions into final_candidate.json
        gen_path = run_dir / "phase2_coherence" / "refined_candidate.json"
        critique_path = run_dir / "phase3_critique" / "phase3_critique_output.json"
        if gen_path.exists():
            print(f"\n  ⚡ Running merger for Phase 3.3...")
            import subprocess
            merge_cmd = [
                "python3", str(SKILL_DIR / "scripts" / "run.py"),
                "phase3_merge_revisions",
                "--phase2", str(gen_path),
                "--revisions", str(output_path),
                "--critique", str(critique_path) if critique_path.exists() else "",
                "--out", str(run_dir / "phase3_revise"),
                "--out-name", "final_candidate.json",
            ]
            if not critique_path.exists():
                merge_cmd.remove("--critique")
                merge_cmd.remove(str(critique_path))
            try:
                subprocess.run(merge_cmd, cwd=str(run_dir.parent.parent), timeout=30)
                if (run_dir / "phase3_revise" / "final_candidate.json").exists():
                    print(f"  ✅ Merger complete")
                else:
                    print(f"  ⚠ Merger may have failed — check final_candidate.json")
            except subprocess.TimeoutExpired:
                print(f"  ⚠ Merger timed out")

    if phase == "4.fill":
        # Validate fill_map format: flat {path: value} dict, no forbidden keys
        forbidden_roots = ["falsification_prediction", "compute_budget"]
        if isinstance(result, dict) and "raw" not in result:
            issues = []
            for k in result:
                if k.split(".")[0] in forbidden_roots or k.split("[")[0] in forbidden_roots:
                    issues.append(k)
            if issues:
                print(f"  WARNING: fill_map contains forbidden keys: {issues}")
                for k in issues:
                    del result[k]
            print(f"  Fill map: {len(result)} entries")
        elif isinstance(result, dict) and "raw" in result:
            print(f"  WARNING: fill_map is raw text ({len(result['raw'])} chars)")

    # ── Write output ──
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n  ✅ Written: {output_path} ({output_path.stat().st_size} bytes)")

    # ── Post-phase actions ──
    if phase == "2.3":
        gen_path = run_dir / "phase2_generate" / "phase2_generate_output.json"
        coh_path = output_path
        print(f"\n  ⚡ Running merger for Phase 2.3...")
        import subprocess
        merge_cmd = [
            "python3", str(SKILL_DIR / "scripts" / "run.py"),
            "phase3_merge_revisions",
            "--phase2", str(gen_path),
            "--revisions", str(coh_path),
            "--out", str(run_dir / "phase2_coherence"),
            "--out-name", "refined_candidate.json",
        ]
        try:
            subprocess.run(merge_cmd, cwd=str(run_dir.parent.parent), timeout=30)
            if (run_dir / "phase2_coherence" / "refined_candidate.json").exists():
                print(f"  ✅ Merger complete")
            else:
                print(f"  ⚠ Merger may have failed — check refined_candidate.json")
        except subprocess.TimeoutExpired:
            print(f"  ⚠ Merger timed out")

    print(f"\n  Done. Phase {phase} complete.\n")


if __name__ == "__main__":
    main()