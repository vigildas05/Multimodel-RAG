"""The gateway: one object the application calls at each point where untrusted text
meets the LLM. Detection results are returned, not logged: the host application decides
how to record them (e.g. as security events in its own database)."""

import time

from security.classifier import InjectionClassifier, NullClassifier
from security.config import GatewayConfig
from security.heuristics import HeuristicScanner, combine_weights
from security.normalize import normalize
from security.output import OutputResult, scan_output
from security.prompt import PromptBundle, SourceBlock, build_plain_prompt, build_prompt
from security.types import Decision, ScanResult


class SecurityGateway:
    def __init__(self, config: GatewayConfig | None = None, classifier: InjectionClassifier | None = None):
        self.config = config or GatewayConfig()
        self._classifier = classifier or NullClassifier()
        self._heuristics = HeuristicScanner(max_length=self.config.max_input_chars)

    # --- detection ---------------------------------------------------------------------

    def scan_input(self, question: str) -> ScanResult:
        """Layer 1: a user's question. BLOCK means refuse to answer."""
        return self._scan(question, self.config.block_threshold)

    def scan_chunk(self, text: str) -> ScanResult:
        """Layer 2: a document chunk at ingest time. BLOCK means quarantine."""
        return self._scan(text, self.config.quarantine_threshold)

    def scan_chunks(self, texts: list[str]) -> list[ScanResult]:
        """Batch version of scan_chunk (one classifier pass for the whole document)."""
        return self._scan_many(texts, self.config.quarantine_threshold)

    def _scan(self, text: str, block_threshold: float) -> ScanResult:
        return self._scan_many([text], block_threshold)[0]

    def _scan_many(self, texts: list[str], block_threshold: float) -> list[ScanResult]:
        if not self.config.enabled:
            return [ScanResult(risk=0.0, decision=Decision.ALLOW) for _ in texts]
        started = time.perf_counter()
        normalized = [normalize(t) for t in texts]
        findings = [n.findings + self._heuristics.scan(n.scan_targets) for n in normalized]
        # The classifier sees the de-obfuscated text plus anything decoded from it.
        probabilities = self._classifier.score(["\n".join(n.scan_targets) for n in normalized])
        per_item_ms = (time.perf_counter() - started) * 1000 / max(len(texts), 1)

        results = []
        for item_findings, p in zip(findings, probabilities):
            heuristic = combine_weights(item_findings)
            classifier = p * self.config.classifier_weight
            risk = 1.0 - (1.0 - heuristic) * (1.0 - classifier)
            if risk >= block_threshold:
                decision = Decision.BLOCK
            elif risk >= self.config.flag_threshold:
                decision = Decision.FLAG
            else:
                decision = Decision.ALLOW
            results.append(
                ScanResult(
                    risk=risk,
                    decision=decision,
                    findings=item_findings,
                    heuristic_score=heuristic,
                    classifier_score=p if not isinstance(self._classifier, NullClassifier) else None,
                    latency_ms=per_item_ms,
                )
            )
        return results

    # --- prompt & output ---------------------------------------------------------------

    def build_prompt(self, question: str, sources: list[SourceBlock]) -> PromptBundle:
        """Layer 3: spotlighted prompt with instruction hierarchy and a fresh canary."""
        if not self.config.enabled:
            return build_plain_prompt(question, sources)
        return build_prompt(question, sources)

    def scan_output(self, text: str, bundle: PromptBundle) -> OutputResult:
        """Layer 4: block prompt leaks, strip images and bad citations, redact PII."""
        if not self.config.enabled:
            return OutputResult(text=text)
        return scan_output(text, bundle, pii_redaction=self.config.pii_redaction)
