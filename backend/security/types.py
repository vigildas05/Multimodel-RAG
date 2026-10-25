from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    FLAG = "flag"  # allowed, but logged for review
    BLOCK = "block"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: str  # e.g. instruction_override, roleplay_jailbreak, prompt_extraction, obfuscation
    weight: float  # 0..1 contribution to the risk score
    excerpt: str = ""


@dataclass
class ScanResult:
    risk: float
    decision: Decision
    findings: list[Finding] = field(default_factory=list)
    heuristic_score: float = 0.0
    classifier_score: float | None = None
    latency_ms: float = 0.0

    @property
    def categories(self) -> list[str]:
        return sorted({f.category for f in self.findings})

    def summary(self) -> dict:
        """JSON-safe description, for security event logs."""
        return {
            "risk": round(self.risk, 3),
            "decision": self.decision.value,
            "heuristic_score": round(self.heuristic_score, 3),
            "classifier_score": None if self.classifier_score is None else round(self.classifier_score, 3),
            "categories": self.categories,
            "rules": sorted({f.rule_id for f in self.findings}),
        }
