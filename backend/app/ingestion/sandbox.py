"""Run untrusted-file parsing in a separate, killable process.

Parsing a malicious file can hang or blow up inside C libraries (MuPDF, image codecs,
FFmpeg), where Python timeouts can't interrupt it. Each parse runs in a fresh child
process ("spawn": no inherited state) that is killed outright if it exceeds its time
budget. On Linux the child's address space is also capped (RLIMIT_AS), so a
decompression bomb fails with MemoryError instead of exhausting the host; Windows has no
equivalent, so there the time limit is the only hard bound.

Targets are named as "module:function" so the child imports only what it needs.
"""

import importlib
import logging
import multiprocessing

from app.core.exceptions import UploadRejectedError

logger = logging.getLogger("app.ingestion.sandbox")

_CTX = multiprocessing.get_context("spawn")


class ParseRejected(Exception):
    """Raised by parsers for a file that violates a limit; the message is user-facing."""


def _child(conn, target: str, args: tuple, memory_limit_mb: int) -> None:
    try:
        if memory_limit_mb:
            try:
                import resource  # POSIX only

                limit = memory_limit_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ImportError, ValueError, OSError):
                pass
        module_name, func_name = target.split(":")
        result = getattr(importlib.import_module(module_name), func_name)(*args)
        conn.send(("ok", result))
    except ParseRejected as exc:
        conn.send(("rejected", str(exc)))
    except MemoryError:
        conn.send(("rejected", "The file needs too much memory to process."))
    except BaseException as exc:  # noqa: BLE001 - report everything, the parent decides
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def run_sandboxed(target: str, args: tuple, timeout_seconds: float, memory_limit_mb: int = 0):
    """Run `target(*args)` in a child process and return its (picklable) result.

    Raises UploadRejectedError (user-facing) for limit violations, timeouts and crashes.
    """
    parent_conn, child_conn = _CTX.Pipe(duplex=False)
    process = _CTX.Process(target=_child, args=(child_conn, target, args, memory_limit_mb), daemon=True)
    process.start()
    child_conn.close()
    try:
        if not parent_conn.poll(timeout_seconds):
            raise UploadRejectedError(
                "Processing this file took too long and was stopped.",
                detail=f"{target} exceeded {timeout_seconds}s",
            )
        try:
            status, payload = parent_conn.recv()
        except EOFError as exc:
            raise UploadRejectedError(
                "This file could not be processed.", detail=f"{target} exited without a result"
            ) from exc
    finally:
        parent_conn.close()
        if process.is_alive():
            process.kill()
        process.join(5)

    if status == "ok":
        return payload
    if status == "rejected":
        raise UploadRejectedError(payload)
    logger.warning("Sandboxed parser %s failed: %s", target, payload)
    raise UploadRejectedError("This file could not be read. It may be corrupted or malformed.", detail=payload)
