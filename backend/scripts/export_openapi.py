"""Write the OpenAPI document to a file (used to generate the front-end API types).

Usage: uv run python scripts/export_openapi.py ../frontend/openapi.json
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from omnisync.api.app import create_app
from omnisync.settings import Settings


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(data_dir=Path(tmp), static_dir=None, instance_name="openapi"))
        schema = app.openapi()
        app.state.omnisync.engine.dispose()
    Path(sys.argv[1]).write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
