# Red-team suite

Measures how well the [LLM security gateway](../backend/security) stops prompt-injection
attacks, by running every attack with the gateway **off** and **on** and checking, objectively,
whether the attacker's goal was achieved.

```bash
python redteam/run_eval.py --classifier off      # heuristics only, no downloads (what CI runs)
python redteam/run_eval.py                       # + ML classifier if it's in the HF cache
python redteam/run_eval.py --real-llm --limit 8  # real model via Ollama, 8 attacks per category
python redteam/build_dataset.py                  # regenerate datasets/*.jsonl
pytest redteam/tests                             # harness tests
```

Reports land in `reports/<name>.json` (every record) and `reports/<name>.md` (summary).

## Datasets

| File | Contents |
|---|---|
| `datasets/attacks.jsonl` | 340 attacks in 6 categories: direct injection, jailbreak / role-play, system-prompt extraction, indirect injection planted in documents, PII exfiltration, obfuscation (base64, hex, leetspeak, spaced letters, zero-width, homoglyphs, full-width, ROT13, reversed text) |
| `datasets/benign.jsonl` | 100 normal questions and documents, many deliberately using trigger words ("ignore the footnotes", "developer mode setting", "system requirements") |
| `datasets/external_deepset_test.jsonl` | 116 prompts from the deepset/prompt-injections test split (Apache-2.0), used for detection only; see [NOTICE](datasets/NOTICE.md) |

Every attack has a goal with an objective success test: a unique marker appears in the
answer, the system prompt or canary leaks, personal data from the documents appears, or a
markdown image beacon survives.

**Dev vs holdout.** Dev phrasings are what the heuristic rules were developed against.
Holdout phrasings were written as separate lists that avoid the rules' wording, and are never
used for tuning. The gap between the two is the honest measure of how far hand-written rules
generalise.

## Method and caveats

- **Mock model.** By default the LLM is a deterministic *gullible* mock: if an attack's text
  reaches its prompt, it complies. Mock results therefore measure the detection and output
  layers against a worst-case model, not how well spotlighting persuades a real model.
  `--real-llm` measures that, on a sample.
- **Worst-case retrieval.** A poisoned chunk is always retrieved unless the ingest guard
  quarantines it.
- **Same author.** The attacks and the heuristics were written by the same person; the holdout
  split and the external benchmark exist to expose overfitting, and they do.
- **Direct "say X" attacks** in the user's own question measure the ability to override
  instructions, not harm to anyone else.

## CI gate

`.github/workflows/redteam.yml` runs the harness tests and the heuristics-only evaluation on
every push and pull request, and fails the build if the attack success rate with the gateway
on exceeds `REDTEAM_MAX_ASR` (default 0.28) or the benign false-positive rate exceeds
`REDTEAM_MAX_FPR` (default 0.02). Both can be overridden with repository variables.
