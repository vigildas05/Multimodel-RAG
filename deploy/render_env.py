"""Write deploy/.env.prod from deploy/.env.prod.example plus secrets in AWS SSM Parameter Store.

Run on the server, whose IAM role can read only the parameter path:

    API_DOMAIN=api.example.com CORS_ORIGINS=https://app.example.com python3 render_env.py

Every SecureString under SSM_PREFIX (default /sentinel-rag), e.g. /sentinel-rag/DATABASE_URL,
becomes DATABASE_URL in the file. JWT_SECRET_KEY and METRICS_TOKEN are generated here on the
first run and kept on later runs (a new JWT key signs everyone out), so re-running after
rotating any other secret is safe. Secret values are never printed; the file is mode 600.
"""

import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / ".env.prod.example"
OUTPUT = HERE / ".env.prod"
REQUIRED = {"DATABASE_URL", "QDRANT_URL", "QDRANT_API_KEY", "LLM_API_KEY"}
GENERATED = ("JWT_SECRET_KEY", "METRICS_TOKEN")
FROM_ENVIRONMENT = ("API_DOMAIN", "CORS_ORIGINS")
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def fetch_parameters(prefix: str, region: str) -> dict[str, str]:
    result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["aws", "ssm", "get-parameters-by-path", "--path", prefix, "--with-decryption",  # noqa: S607
         "--region", region, "--output", "json"],
        check=True, capture_output=True, text=True,
    )
    return {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in json.loads(result.stdout)["Parameters"]}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := _LINE.match(line):
                values[m[1]] = m[2].strip().strip("'")
    return values


def quote(key: str, value: str) -> str:
    # Single quotes: Docker Compose reads the value literally (no $-interpolation).
    if "'" in value or "\n" in value:
        sys.exit(f"{key}: values must not contain single quotes or newlines")
    return f"'{value}'"


def main() -> None:
    prefix = os.environ.get("SSM_PREFIX", "/sentinel-rag")
    params = fetch_parameters(prefix, os.environ.get("AWS_REGION", "ap-southeast-2"))
    if missing := sorted(REQUIRED - params.keys()):
        sys.exit(f"Missing parameters under {prefix}: {missing}")
    for key in FROM_ENVIRONMENT:
        if not os.environ.get(key):
            sys.exit(f"Set {key} in the environment")

    existing = read_env(OUTPUT)
    values = dict(params)
    values.update({key: os.environ[key] for key in FROM_ENVIRONMENT})
    created = []
    for key in GENERATED:
        if key not in values:
            if existing.get(key):
                values[key] = existing[key]
            else:
                values[key] = secrets.token_urlsafe(48)
                created.append(key)

    lines, written = [], set()
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if not m:
            lines.append(line)
            continue
        key = m[1]
        value = values.get(key, re.split(r"\s+#", m[2], maxsplit=1)[0].strip())
        lines.append(f"{key}={quote(key, value) if value else ''}")
        written.add(key)
    extra = [k for k in values if k not in written]
    if extra:
        lines += ["", "# --- From Parameter Store ---"] + [f"{k}={quote(k, values[k])}" for k in sorted(extra)]

    fd = os.open(OUTPUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(OUTPUT, 0o600)
    print(f"Wrote {OUTPUT} (mode 600) with {len(written) + len(extra)} settings.")
    print(f"From Parameter Store: {sorted(params)}")
    print(f"Generated now: {created or 'none (kept existing)'}")


if __name__ == "__main__":
    main()
