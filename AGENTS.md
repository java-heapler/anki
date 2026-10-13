# Repository instructions

Maintain the standalone src-layout/hatchling/uv CLI and skills/anki/SKILL.md architecture shared with apple-books. Keep JSON CLI examples and skill guidance aligned. Use synthetic tests and a local stub server; never write to a personal Anki collection for testing. AnkiConnect writes require explicit --confirm and session authorization; do not retry uncertain writes. Keep API keys, personal notes, exports, and databases out of git. Run uv run pytest --cov=anki_agent --cov-branch and uv build after code changes. Do not change superai-skills.
