"""
Write the website's API description to frontend/openapi.json, for `npm run gen:api`.

    uv run scripts/export_openapi.py && (cd frontend && npm run gen:api)

Run both after changing an endpoint or a response model, and commit the results.
"""

import json
import tempfile
from pathlib import Path

from beans_bot.config.schema import BotConfig
from beans_bot.services import Services
from beans_bot.web.app import create_app
from beans_bot.web.context import WebContext

OUTPUT = Path(__file__).resolve().parent.parent / "frontend" / "openapi.json"


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        # A throwaway database: building the app never queries it, but Services wants one.
        config = BotConfig(bot_token="unused", db_path=Path(scratch) / "openapi.db")
        config.web.static_dir = Path(scratch) / "no-frontend"
        services = Services.build(config)
        app = create_app(WebContext(config=config, services=services))
        OUTPUT.write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
