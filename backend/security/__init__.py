"""Sentinel LLM security gateway.

A self-contained package (no imports from the application) that a RAG app wraps around
its LLM calls:

    input guard   -> scan_input():   direct prompt injection in user questions
    ingest guard  -> scan_chunk():   indirect injection planted in documents (quarantine)
    prompt layer  -> build_prompt(): spotlighted data blocks, instruction hierarchy, canary
    output guard  -> scan_output():  canary/system-prompt leaks, PII redaction, citations

Each layer is defence in depth, not a guarantee: no prompt-injection defence is complete.
"""

from security.types import Decision, Finding, ScanResult

__all__ = ["Decision", "Finding", "ScanResult"]
