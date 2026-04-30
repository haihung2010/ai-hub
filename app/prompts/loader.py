"""Load per-project system prompts from Markdown files with YAML frontmatter.

The loader is intentionally dependency-free: we parse a tiny subset of YAML
(flat scalar key/value pairs) rather than pull in PyYAML for three fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.errors import ProjectNotFound

_PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class ProjectPrompt:
    project_id: str
    model: str
    provider: str
    temperature: float
    system_prompt: str
    enable_search: bool = True


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw.strip()
    _, header, body = parts
    meta: dict[str, str] = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body.strip()


@lru_cache(maxsize=None)
def load_prompt(project_id: str) -> ProjectPrompt:
    if not project_id.isidentifier():
        raise ProjectNotFound(project_id)

    path = _PROMPTS_DIR / f"{project_id}.md"
    if not path.is_file():
        raise ProjectNotFound(project_id)

    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    if not body:
        raise ProjectNotFound(f"{project_id}: empty prompt body")

    try:
        temperature = float(meta.get("temperature", "0.7"))
    except ValueError:
        temperature = 0.7

    enable_search_raw = meta.get("enable_search", "true").lower()
    enable_search = enable_search_raw not in ("false", "0", "no")

    return ProjectPrompt(
        project_id=project_id,
        model=meta.get("model", ""),
        provider=meta.get("provider", "local").lower(),
        temperature=temperature,
        system_prompt=body,
        enable_search=enable_search,
    )
