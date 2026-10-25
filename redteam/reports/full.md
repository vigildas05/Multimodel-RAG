# Red-team report

Generated 2026-10-25T15:56:50+00:00 (commit `3c05413`).
LLM: **mock-gullible**. Classifier: **protectai/deberta-v3-base-prompt-injection-v2**. Thresholds: block 0.8, quarantine 0.75, classifier weight 0.7.
Dataset: 340 attacks, 100 benign items, 116 external (deepset).

## Attack success rate, security OFF vs ON

| Category | n | ASR off | ASR on | Blocked/quarantined | Stopped by output guard |
|---|---:|---:|---:|---:|---:|
| direct_injection | 60 | 100.0% | 36.7% | 63.3% | 0 |
| indirect_injection | 72 | 100.0% | 29.2% | 70.8% | 0 |
| jailbreak_roleplay | 56 | 100.0% | 37.5% | 62.5% | 0 |
| obfuscation | 46 | 100.0% | 30.4% | 69.6% | 0 |
| pii_exfiltration | 50 | 100.0% | 0.0% | 8.0% | 46 |
| prompt_extraction | 56 | 100.0% | 0.0% | 60.7% | 22 |
| **overall** | **340** | **100.0%** | **22.9%** | **57.1%** | **68** |

## Dev vs holdout (security ON)

Holdout phrasings were written separately and never used to tune the rules.

| Split | n | ASR on | Blocked/quarantined |
|---|---:|---:|---:|
| dev | 228 | 3.1% | 82.0% |
| holdout | 112 | 63.4% | 6.2% |

## False positives (benign set, security ON)

| Set | n | False-positive rate | Flagged (allowed, logged) |
|---|---:|---:|---:|
| questions (blocked) | 70 | 0.0% | 11.4% |
| documents (quarantined) | 30 | 0.0% | 13.3% |
| **overall** | **100** | **0.0%** | **12.0%** |

## External benchmark: deepset/prompt-injections (test split)

Detection only (no success markers). Multilingual (EN/DE), broad definition of injection.

| Label | n | Blocked | Flagged or blocked |
|---|---:|---:|---:|
| attack | 60 | 10.0% | 41.7% |
| benign | 56 | 0.0% | 0.0% |

## Added latency (security ON, per request)

| | mean | p50 | p95 |
|---|---:|---:|---:|
| attacks | 145.052 ms | 134.828 ms | 202.622 ms |
| benign | 151.392 ms | 148.575 ms | 183.046 ms |

## How to read this

- *ASR*: share of attacks whose objective succeeded (marker in the answer, system prompt or
  canary leaked, personal data from the documents in the answer, or an image beacon left in).
- *Blocked/quarantined*: stopped by the input guard (questions) or ingest guard (documents).
- The LLM is a deterministic **gullible mock** that obeys any attack text that reaches it,
  so these numbers measure the detection and output layers against a worst-case model. They
  do not measure how much spotlighting helps a real model resist injected text.
- Retrieval is simulated as worst case: a poisoned chunk is always retrieved unless quarantined.
- The attack set was written by the same person who wrote the heuristics; the holdout split and
  the external benchmark exist to expose overfitting.
