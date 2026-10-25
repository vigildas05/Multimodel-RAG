"""Red-team evaluation: security gateway OFF vs ON, over the attack, benign and external sets.

    python redteam/run_eval.py                         # mock LLM, classifier if cached
    python redteam/run_eval.py --classifier off        # heuristics only (what CI runs)
    python redteam/run_eval.py --max-asr 0.05 --max-fpr 0.05   # fail (exit 1) above these
    python redteam/run_eval.py --real-llm --limit 5    # real model (Ollama), 5 per category

Writes <out-dir>/<name>.json and <name>.md. Nothing in the reports is estimated: every
number is computed from this run's results.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness  # noqa: E402
from security.classifier import HuggingFaceClassifier, NullClassifier  # noqa: E402
from security.config import GatewayConfig  # noqa: E402
from security.gateway import SecurityGateway  # noqa: E402

DATASETS = HERE / "datasets"
MODEL = "protectai/deberta-v3-base-prompt-injection-v2"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def classifier_available() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        import transformers  # noqa: F401

        return isinstance(try_to_load_from_cache(MODEL, "config.json"), str)
    except Exception:  # noqa: BLE001
        return False


class RealLLM:
    """Adapter for the app's Ollama provider, with the same interface as the mock."""

    def __init__(self, base_url: str, model: str):
        from app.services.llm.ollama_provider import OllamaProvider

        self._provider = OllamaProvider(base_url=base_url, text_model=model, vision_model=model, timeout=180)
        self.name = f"ollama:{model}"

    def __call__(self, record):  # factory signature used by the harness
        return self

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        result = asyncio.run(
            self._provider.generate(
                system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.0, top_p=1.0, max_output_tokens=300
            )
        )
        return result.text


def sample(records: list[dict], limit: int | None) -> list[dict]:
    if not limit:
        return records
    per_cat: Counter = Counter()
    picked = []
    for r in records:
        if per_cat[r["category"]] < limit:
            per_cat[r["category"]] += 1
            picked.append(r)
    return picked


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip() or None
    except OSError:
        return None


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_markdown(report: dict) -> str:
    cfg, off, on = report["config"], report["attacks"]["off"], report["attacks"]["on"]
    lines = [
        "# Red-team report",
        "",
        f"Generated {report['generated_at']} (commit `{report.get('commit') or 'unknown'}`).",
        f"LLM: **{cfg['llm']}**. Classifier: **{cfg['classifier']}**. "
        f"Thresholds: block {cfg['block_threshold']}, quarantine {cfg['quarantine_threshold']}, "
        f"classifier weight {cfg['classifier_weight']}.",
        f"Dataset: {report['dataset']['attacks']} attacks, {report['dataset']['benign']} benign items"
        + (f", {report['dataset']['external']} external (deepset)" if report["dataset"].get("external") else "")
        + ".",
        "",
        "## Attack success rate, security OFF vs ON",
        "",
        "| Category | n | ASR off | ASR on | Blocked/quarantined | Stopped by output guard |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cat in sorted(on["by_category"]):
        o, n = off["by_category"][cat], on["by_category"][cat]
        out_guard = n["stopped_by"].get("output_guard_block", 0) + n["stopped_by"].get("output_guard_sanitized", 0)
        lines.append(f"| {cat} | {n['n']} | {pct(o['asr'])} | {pct(n['asr'])} | {pct(n['block_rate'])} | {out_guard} |")
    o, n = off["overall"], on["overall"]
    out_guard = n["stopped_by"].get("output_guard_block", 0) + n["stopped_by"].get("output_guard_sanitized", 0)
    lines += [
        f"| **overall** | **{n['n']}** | **{pct(o['asr'])}** | **{pct(n['asr'])}** | **{pct(n['block_rate'])}** | **{out_guard}** |",
        "",
        "## Dev vs holdout (security ON)",
        "",
        "Holdout phrasings were written separately and never used to tune the rules.",
        "",
        "| Split | n | ASR on | Blocked/quarantined |",
        "|---|---:|---:|---:|",
    ]
    for split, s in on["by_split"].items():
        lines.append(f"| {split} | {s['n']} | {pct(s['asr'])} | {pct(s['block_rate'])} |")

    b = report["benign"]
    lines += [
        "",
        "## False positives (benign set, security ON)",
        "",
        "| Set | n | False-positive rate | Flagged (allowed, logged) |",
        "|---|---:|---:|---:|",
        f"| questions (blocked) | {b['questions']['n']} | {pct(b['questions']['fpr'])} | {pct(b['questions']['flag_rate'])} |",
        f"| documents (quarantined) | {b['documents']['n']} | {pct(b['documents']['fpr'])} | {pct(b['documents']['flag_rate'])} |",
        f"| **overall** | **{b['overall']['n']}** | **{pct(b['overall']['fpr'])}** | **{pct(b['overall']['flag_rate'])}** |",
    ]
    if b["false_positive_ids"]:
        lines.append(f"\nFalse positives: {', '.join(b['false_positive_ids'])}")

    ext = report.get("external")
    if ext:
        lines += [
            "",
            "## External benchmark: deepset/prompt-injections (test split)",
            "",
            "Detection only (no success markers). Multilingual (EN/DE), broad definition of injection.",
            "",
            "| Label | n | Blocked | Flagged or blocked |",
            "|---|---:|---:|---:|",
        ]
        for label in ("attack", "benign"):
            if label in ext:
                e = ext[label]
                lines.append(f"| {label} | {e['n']} | {pct(e['block_rate'])} | {pct(e['flag_or_block_rate'])} |")

    lat_a, lat_b = on["overall"]["latency"], b["overall"]["latency"]
    lines += [
        "",
        "## Added latency (security ON, per request)",
        "",
        "| | mean | p50 | p95 |",
        "|---|---:|---:|---:|",
        f"| attacks | {lat_a['mean_ms']} ms | {lat_a['p50_ms']} ms | {lat_a['p95_ms']} ms |",
        f"| benign | {lat_b['mean_ms']} ms | {lat_b['p50_ms']} ms | {lat_b['p95_ms']} ms |",
        "",
        "## How to read this",
        "",
        "- *ASR*: share of attacks whose objective succeeded (marker in the answer, system prompt or",
        "  canary leaked, personal data from the documents in the answer, or an image beacon left in).",
        "- *Blocked/quarantined*: stopped by the input guard (questions) or ingest guard (documents).",
    ]
    if cfg["llm"].startswith("mock"):
        lines += [
            "- The LLM is a deterministic **gullible mock** that obeys any attack text that reaches it,",
            "  so these numbers measure the detection and output layers against a worst-case model. They",
            "  do not measure how much spotlighting helps a real model resist injected text.",
        ]
    lines += [
        "- Retrieval is simulated as worst case: a poisoned chunk is always retrieved unless quarantined.",
        "- The attack set was written by the same person who wrote the heuristics; the holdout split and",
        "  the external benchmark exist to expose overfitting.",
        "",
    ]
    if report.get("gate"):
        g = report["gate"]
        lines += ["## CI gate", "", f"{'PASSED' if g['passed'] else 'FAILED'}: " + "; ".join(g["checks"]), ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--classifier", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--real-llm", action="store_true", help="use Ollama instead of the gullible mock")
    ap.add_argument("--llm-url", default="http://localhost:11434")
    ap.add_argument("--llm-model", default="llama3.2:3b")
    ap.add_argument("--limit", type=int, help="max attacks per category (useful with --real-llm)")
    ap.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    ap.add_argument("--no-external", action="store_true")
    ap.add_argument("--out-dir", default=str(HERE / "reports"))
    ap.add_argument("--name", help="report file name (default depends on mode)")
    ap.add_argument("--classifier-weight", type=float, help="override GatewayConfig.classifier_weight (sensitivity runs)")
    ap.add_argument("--max-asr", type=float, help="fail if overall ASR with security ON exceeds this")
    ap.add_argument("--max-fpr", type=float, help="fail if the benign false-positive rate exceeds this")
    args = ap.parse_args(argv)

    use_classifier = args.classifier == "on" or (args.classifier == "auto" and classifier_available())
    classifier = HuggingFaceClassifier(MODEL) if use_classifier else NullClassifier()
    if use_classifier:
        classifier.warm_up()
    config = GatewayConfig() if args.classifier_weight is None else GatewayConfig(classifier_weight=args.classifier_weight)
    gateway = SecurityGateway(config, classifier)

    attacks = load_jsonl(DATASETS / "attacks.jsonl")
    benign = load_jsonl(DATASETS / "benign.jsonl")
    if args.split != "all":
        attacks = [r for r in attacks if r["split"] == args.split]
        benign = [r for r in benign if r["split"] == args.split]
    attacks = sample(attacks, args.limit)
    external_path = DATASETS / "external_deepset_test.jsonl"
    external = load_jsonl(external_path) if external_path.exists() and not args.no_external else []

    llm_factory = RealLLM(args.llm_url, args.llm_model) if args.real_llm else harness.GullibleLLM
    llm_name = llm_factory.name if args.real_llm else harness.GullibleLLM.name

    print(f"Running {len(attacks)} attacks with security OFF...", flush=True)
    off_results, off_s = harness.run(attacks, None, llm_factory)
    print(f"Running {len(attacks)} attacks with security ON (classifier {'on' if use_classifier else 'off'})...", flush=True)
    on_results, on_s = harness.run(attacks, gateway, llm_factory)
    benign_results = [harness.evaluate_benign(r, gateway) for r in benign]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": git_commit(),
        "config": {
            "llm": llm_name,
            "classifier": MODEL if use_classifier else "off (heuristics only)",
            "block_threshold": config.block_threshold,
            "flag_threshold": config.flag_threshold,
            "quarantine_threshold": config.quarantine_threshold,
            "classifier_weight": config.classifier_weight,
            "split": args.split,
            "limit_per_category": args.limit,
        },
        "dataset": {"attacks": len(attacks), "benign": len(benign), "external": len(external)},
        "attacks": {"off": harness.summarize_attacks(off_results), "on": harness.summarize_attacks(on_results)},
        "benign": harness.summarize_benign(benign_results),
        "external": harness.evaluate_external(external, gateway) if external else None,
        "runtime_seconds": {"off": round(off_s, 2), "on": round(on_s, 2)},
        "records": {"on": harness.as_dicts(on_results), "off": harness.as_dicts(off_results)},
    }

    checks, passed = [], True
    asr_on, fpr = report["attacks"]["on"]["overall"]["asr"], report["benign"]["overall"]["fpr"]
    if args.max_asr is not None:
        ok = asr_on <= args.max_asr
        passed &= ok
        checks.append(f"ASR on {pct(asr_on)} {'<=' if ok else '>'} max {pct(args.max_asr)}")
    if args.max_fpr is not None:
        ok = fpr <= args.max_fpr
        passed &= ok
        checks.append(f"FPR {pct(fpr)} {'<=' if ok else '>'} max {pct(args.max_fpr)}")
    if checks:
        report["gate"] = {"passed": passed, "checks": checks}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or ("real_llm" if args.real_llm else ("full" if use_classifier else "heuristics_only"))
    (out_dir / f"{name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    (out_dir / f"{name}.md").write_text(markdown, encoding="utf-8")

    o, n = report["attacks"]["off"]["overall"], report["attacks"]["on"]["overall"]
    print(f"ASR off {pct(o['asr'])} -> on {pct(n['asr'])}; blocked/quarantined {pct(n['block_rate'])}; "
          f"benign FPR {pct(fpr)}; wrote {out_dir / name}.md")
    for check in checks:
        print(("PASS " if passed else "GATE ") + check)
    return 0 if passed else 1


if __name__ == "__main__":
    t = time.perf_counter()
    code = main()
    print(f"done in {time.perf_counter() - t:.1f}s")
    sys.exit(code)
