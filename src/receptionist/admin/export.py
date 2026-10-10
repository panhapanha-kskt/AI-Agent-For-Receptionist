"""Move an approved answer into its skill's faq.md (permanent, version-controlled).

Approved answers live in the database; frequently used ones belong in the skill files,
where they are reviewed in git and also teach the router (FAQ headings are routing
examples). The file path always comes from the skill registry, never from the request.
"""

import re

from receptionist.knowledge.models import KnowledgeEntry
from receptionist.skills.loader import MAX_FILE_BYTES, SkillRegistry

FAQ_FILE = "faq.md"


class ExportError(ValueError):
    pass


def _one_line(text: str) -> str:
    return " ".join(text.split()).lstrip("#").strip()[:300]


def _safe_body(text: str) -> str:
    # Lines starting with "#" would become headings (= new FAQ questions); escape them.
    return re.sub(r"(?m)^\s*#", r"\\#", text.strip())


def export_to_faq(registry: SkillRegistry, entry: KnowledgeEntry) -> str:
    skill = registry.get(entry.skill)
    if skill is None or skill.directory is None:
        raise ExportError(f"Skill {entry.skill!r} no longer exists.")
    question = _one_line(entry.question)
    if not question:
        raise ExportError("The question is empty.")
    path = skill.directory / FAQ_FILE
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    header = "" if existing else f"# {skill.name}: frequently asked questions\n"
    block = f"{header}\n## {question}\n{_safe_body(entry.answer)}\n"
    if len((existing + block).encode("utf-8")) > MAX_FILE_BYTES:
        raise ExportError(f"{FAQ_FILE} for {skill.name} is full; split it into another file.")
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        # e.g. the skills folder is mounted read-only in Docker.
        raise ExportError(f"Could not write {FAQ_FILE}: the skills folder is read-only.") from exc
    return f"{skill.name}/{FAQ_FILE}"
