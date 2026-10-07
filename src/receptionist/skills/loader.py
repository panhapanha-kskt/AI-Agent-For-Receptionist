"""Skill registry, following the Agent Skills progressive-disclosure pattern.

Each skill is a folder under SKILLS_DIR containing a SKILL.md with YAML frontmatter
(`name`, `description`) and optional reference files (.md / .yaml / .txt).

Level 1: name + description go into the system prompt (`index()`).
Level 2: the agent loads the SKILL.md body on demand (`load()`).
Level 3: the agent reads a specific reference file on demand (`read_file()`).

Security: the agent can only address skills and files that were discovered at load
time. It never passes a path to the filesystem, so path traversal is impossible.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SKILL_FILE = "SKILL.md"
ALLOWED_REF_SUFFIXES = {".md", ".yaml", ".yml", ".txt"}
MAX_FILE_BYTES = 200_000
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    files: dict[str, Path] = field(default_factory=dict)


def parse_skill_md(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillError("SKILL.md must start with YAML frontmatter between '---' lines")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise SkillError("frontmatter must be a mapping")
    return meta, match.group(2).strip()


def _load_skill_dir(directory: Path) -> Skill:
    skill_md = directory / SKILL_FILE
    if skill_md.stat().st_size > MAX_FILE_BYTES:
        raise SkillError(f"{skill_md} is too large")
    meta, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    if not _NAME_RE.match(name):
        raise SkillError(f"{skill_md}: invalid skill name {name!r}")
    if not description:
        raise SkillError(f"{skill_md}: description is required")

    files: dict[str, Path] = {}
    root = directory.resolve()
    for path in sorted(directory.iterdir()):
        if path.name == SKILL_FILE or path.suffix.lower() not in ALLOWED_REF_SUFFIXES:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if path.resolve().parent != root or path.stat().st_size > MAX_FILE_BYTES:
            continue
        files[path.name] = path.resolve()

    return Skill(name=name, description=description, body=body, files=files)


class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._skills: dict[str, Skill] = {}
        self.errors: list[str] = []

    def reload(self) -> None:
        skills: dict[str, Skill] = {}
        errors: list[str] = []
        if self.skills_dir.is_dir():
            for directory in sorted(self.skills_dir.iterdir()):
                # Folders starting with "_" (e.g. _template) are not live skills.
                if not directory.is_dir() or directory.name.startswith(("_", ".")):
                    continue
                if not (directory / SKILL_FILE).is_file():
                    continue
                try:
                    skill = _load_skill_dir(directory)
                except (SkillError, OSError, yaml.YAMLError) as exc:
                    errors.append(f"{directory.name}: {exc}")
                    continue
                if skill.name in skills:
                    errors.append(f"{directory.name}: duplicate skill name {skill.name!r}")
                    continue
                skills[skill.name] = skill
        self._skills = skills
        self.errors = errors

    def names(self) -> list[str]:
        return sorted(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def index(self) -> str:
        """Level 1: one line per skill for the system prompt."""
        return "\n".join(f"- {s.name}: {s.description}" for s in self._skills.values())

    def load(self, name: str) -> str:
        """Level 2: the skill's instructions plus the list of its reference files."""
        skill = self._skills.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill {name!r}. Available: {', '.join(self.names())}")
        listing = ", ".join(skill.files) or "(none)"
        return f"{skill.body}\n\nReference files for this skill: {listing}"

    def read_file(self, name: str, filename: str) -> str:
        """Level 3: one reference file, looked up by name (never by path)."""
        skill = self._skills.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill {name!r}")
        path = skill.files.get(filename)
        if path is None:
            raise SkillError(f"Skill {name!r} has no file {filename!r}")
        return path.read_text(encoding="utf-8")
