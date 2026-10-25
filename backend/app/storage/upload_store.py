"""Raw uploads on disk, waiting to be processed.

- Stored under <root>/<workspace_id>/<random hex>, with no extension and nothing taken
  from the user's filename, so a filename can never influence the path (no traversal,
  no overwrites, no executable extensions). The original name is display metadata only.
- The root lives in DATA_DIR, outside anything the web server serves.
- Files are deleted as soon as processing finishes (success or failure): the extracted
  chunks are what the app needs, and raw files that aren't kept can't leak.
"""

import shutil
import uuid
from pathlib import Path


class UploadStore:
    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, workspace_id: str, data: bytes) -> str:
        """Write `data` and return its store-relative path (what the database records)."""
        relative = f"{uuid.UUID(workspace_id)}/{uuid.uuid4().hex}"
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative

    def path(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Upload path escapes the upload directory.")
        return path

    def delete(self, relative: str | None) -> None:
        if relative:
            self.path(relative).unlink(missing_ok=True)

    def delete_workspace(self, workspace_id: str) -> None:
        shutil.rmtree(self.path(str(uuid.UUID(workspace_id))), ignore_errors=True)
