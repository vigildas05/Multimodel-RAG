"""Fast, explainable first-stage detector: weighted regex rules per attack category.

Weights encode confidence. A strong rule (e.g. "ignore all previous instructions") is
enough to block on its own; weaker signals ("pretend to be") only matter in combination
or together with the ML classifier. Rules deliberately target instruction-shaped
phrasing (verb + "instructions/prompt/rules") so ordinary research questions that happen
to use words like "ignore" or "system" don't trip them.
"""

import re
from dataclasses import dataclass

from security.types import Finding

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_SPACED_LETTERS = re.compile(r"\b(?:[a-z]\s){3,}[a-z]\b")

_PREV = r"(?:all\s+|any\s+|the\s+|your\s+|my\s+|of\s+the\s+)*(?:previous|prior|above|earlier|preceding|foregoing|original|initial|system|existing)"
_INSTR = r"(?:instructions?|prompts?|rules|directives?|guidelines|commands?|context|messages?|constraints)"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    weight: float
    pattern: re.Pattern


def _r(rule_id: str, category: str, weight: float, pattern: str) -> Rule:
    return Rule(rule_id, category, weight, re.compile(pattern, re.IGNORECASE | re.DOTALL))


RULES: list[Rule] = [
    # --- direct instruction override ---
    _r("ignore_previous", "instruction_override", 0.9, rf"\b(?:ignore|disregard|forget|skip|bypass|override|neglect)\b.{{0,20}}\b{_PREV}\b.{{0,15}}\b{_INSTR}"),
    _r("forget_everything", "instruction_override", 0.75, r"\bforget\s+(?:everything|all\s+(?:that|of\s+that|you\s+(?:were|have\s+been)\s+told))"),
    _r("new_instructions", "instruction_override", 0.6, r"\b(?:new|updated|real|actual|revised)\s+(?:system\s+)?(?:instructions?|rules|directives?)\s*(?::|are|follow)"),
    _r("from_now_on", "instruction_override", 0.45, r"\bfrom\s+now\s+on,?\s+(?:you|always|never|only|respond|answer|act)"),
    _r("do_not_follow", "instruction_override", 0.7, rf"\b(?:do\s+not|don'?t|stop|no\s+longer)\s+(?:follow|obey|adhere\s+to|comply\s+with)\b.{{0,20}}\b{_INSTR}"),
    _r("instead_do", "instruction_override", 0.35, r"\binstead,?\s+(?:you\s+(?:must|should|will)|respond\s+with|output|say|print|write)\b"),
    # --- role-play / jailbreak ---
    _r("dan", "roleplay_jailbreak", 0.85, r"\b(?:DAN|do\s+anything\s+now|STAN|DUDE|AIM\s+mode)\b"),
    _r("developer_mode", "roleplay_jailbreak", 0.8, r"\b(?:developer|debug|god|admin|sudo|unrestricted|jailbreak|jailbroken|evil)\s+mode\b"),
    _r("you_are_now", "roleplay_jailbreak", 0.6, r"\byou\s+are\s+(?:now|no\s+longer)\b"),
    _r("unrestricted_ai", "roleplay_jailbreak", 0.75, r"\b(?:no|without|free\s+(?:of|from))\s+(?:any\s+)?(?:restrictions|limitations|filters|guardrails|censorship|ethical\s+(?:guidelines|constraints)|content\s+polic(?:y|ies))\b"),
    _r("pretend_roleplay", "roleplay_jailbreak", 0.35, r"\b(?:pretend|imagine|act\s+as\s+if|roleplay|role-play)\b.{0,30}\b(?:you\s+are|you're|to\s+be)\b"),
    _r("act_as_persona", "roleplay_jailbreak", 0.45, r"\bact\s+as\s+(?:an?\s+)?(?:unfiltered|uncensored|unrestricted|evil|malicious|rogue|different)\b"),
    _r("hypothetical_bypass", "roleplay_jailbreak", 0.4, r"\b(?:hypothetically|in\s+a\s+fictional\s+world|for\s+a\s+story)\b.{0,60}\b(?:no\s+rules|ignore|without\s+(?:restrictions|limits))"),
    # --- system prompt extraction ---
    _r("reveal_prompt", "prompt_extraction", 0.85, r"\b(?:reveal|show|print|display|output|repeat|recite|leak|dump|give\s+me|tell\s+me|share|write\s+out)\b.{0,25}\b(?:system|initial|original|hidden|secret|internal|developer|full)\s+(?:prompt|instructions?|message|rules|configuration)"),
    _r("what_are_instructions", "prompt_extraction", 0.6, r"\bwhat\s+(?:are|were|is)\s+your\s+(?:system\s+)?(?:instructions?|prompt|rules|directives)\b"),
    _r("repeat_above", "prompt_extraction", 0.7, r"\b(?:repeat|print|output|copy)\b.{0,20}\b(?:everything|all|the\s+(?:text|words|content))\s+(?:above|before|preceding|so\s+far)"),
    _r("verbatim_prefix", "prompt_extraction", 0.55, r"\b(?:begin|start)\s+your\s+(?:response|answer|reply)\s+with\b"),
    _r("canary_probe", "prompt_extraction", 0.6, r"\b(?:canary|secret\s+(?:token|key|code)|confidential\s+reference)\b"),
    # --- data exfiltration ---
    _r("markdown_image_exfil", "data_exfiltration", 0.8, r"!\[[^\]]*\]\(\s*https?://[^)\s]*[?&][^)\s]*="),
    _r("send_data_to", "data_exfiltration", 0.6, r"\b(?:send|post|upload|forward|transmit|exfiltrate|leak)\b.{0,40}\b(?:to|at)\s+(?:https?://|www\.|[\w.-]+\.(?:com|net|org|io|xyz|ru)\b|[\w.+-]+@[\w-]+\.)"),
    _r("collect_user_data", "data_exfiltration", 0.5, r"\b(?:list|collect|extract|dump)\b.{0,30}\b(?:all\s+)?(?:users?|customers?|employees?)'?s?\b.{0,20}\b(?:emails?|passwords?|phone\s+numbers?|credit\s+cards?|aadhaar|pan\s+numbers?|addresses)"),
    # --- fake role / delimiter injection ---
    _r("fake_role_marker", "delimiter_injection", 0.7, r"(?:<\|im_(?:start|end)\|>|\[/?INST\]|<<\s*/?SYS\s*>>|<\|(?:system|assistant|user)\|>)"),
    _r("fake_system_tag", "delimiter_injection", 0.6, r"</?\s*(?:system|instructions?|admin|developer)\s*>"),
    _r("role_prefix_line", "delimiter_injection", 0.45, r"(?:^|\n)\s*(?:#{1,3}\s*)?(?:system|assistant|developer)\s*(?:prompt|message)?\s*:"),
    _r("close_document_block", "delimiter_injection", 0.75, r"</\s*document\b|\bEND\s+OF\s+(?:DOCUMENT|CONTEXT|DATA)\b"),
]


def _variants(text: str) -> list[str]:
    lowered = text.lower()
    variants = [lowered]
    leet = lowered.translate(_LEET)
    if leet != lowered:
        variants.append(leet)
    # "i g n o r e  a l l" -> "ignore all"
    collapsed = _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), leet)
    if collapsed != leet:
        variants.append(collapsed)
    return variants


class HeuristicScanner:
    def __init__(self, rules: list[Rule] | None = None, max_length: int = 4000):
        self._rules = rules or RULES
        self._max_length = max_length

    def scan(self, texts: list[str]) -> list[Finding]:
        """Scan the normalised text and any decoded payloads; each rule reports once."""
        findings: dict[str, Finding] = {}
        for text in texts:
            for variant in _variants(text):
                for rule in self._rules:
                    if rule.rule_id in findings:
                        continue
                    match = rule.pattern.search(variant)
                    if match:
                        findings[rule.rule_id] = Finding(rule.rule_id, rule.category, rule.weight, match.group(0)[:120])
        if texts and len(texts[0]) > self._max_length:
            findings["abnormal_length"] = Finding(
                "abnormal_length", "abnormal_input", 0.3, f"{len(texts[0])} characters"
            )
        return list(findings.values())


def combine_weights(findings: list[Finding]) -> float:
    """Noisy-OR: independent signals reinforce each other but never exceed 1."""
    remaining = 1.0
    for f in findings:
        remaining *= 1.0 - f.weight
    return 1.0 - remaining
