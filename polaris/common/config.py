"""Central place environment variables are read from. Loads .env via
python-dotenv if present. Nothing in this repo reads os.environ directly
outside this module and llm_classifier.py's Azure client construction --
grep for os.environ if that ever needs auditing.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("POLARIS_DATABASE_URL", "sqlite:///polaris_registry.db")
AZURE_OPENAI_CONFIGURED = bool(os.environ.get("AZURE_OPENAI_API_KEY"))
