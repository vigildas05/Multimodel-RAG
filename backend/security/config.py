from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    # Master switch (the red-team suite runs with it off and on to measure the difference).
    enabled: bool = True
    # risk >= block_threshold blocks a question; >= flag_threshold allows it but logs it.
    block_threshold: float = 0.8
    flag_threshold: float = 0.5
    # A document chunk with risk >= quarantine_threshold is excluded from retrieval.
    quarantine_threshold: float = 0.75
    # The classifier's probability is scaled by this before combining. Below 1, a
    # classifier-only detection can flag but never block or quarantine on its own: the
    # model produces confident false positives on some benign text (measured: e.g. "Does
    # the report mention any password policy?" scored 1.0), so it needs a heuristic signal
    # to corroborate it.
    classifier_weight: float = 0.7
    # Questions longer than this get an "abnormal length" signal.
    max_input_chars: int = 4000
    pii_redaction: bool = True
