"""Write the OpenAPI schema to a file (input for ``make gen-client``).

Usage: ``uv run python -m app.scripts.export_openapi ../../packages/api-client/openapi.json``
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def export(path: Path) -> None:
    schema = create_app().openapi()
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export(Path(sys.argv[1]))
