"""`uv run python -m docagent_db` applies pending migrations (see docagent_db.migrate)."""

import sys

from docagent_db.migrate import main

sys.exit(main())
