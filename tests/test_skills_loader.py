import pytest

from receptionist.skills.loader import SkillError, SkillRegistry, parse_skill_md
from tests.conftest import ROOT


def make_skill(root, folder, name, description="Test skill", body="Body", files=None):
    d = root / folder
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8"
    )
    for fname, content in (files or {}).items():
        (d / fname).write_text(content, encoding="utf-8")
    return d


def test_real_skills_load_without_errors():
    reg = SkillRegistry(ROOT / "skills")
    reg.reload()
    assert reg.errors == []
    assert {"admissions", "fees-and-payments", "campus-and-contact"} <= set(reg.names())
    assert "new-skill-name" not in reg.names()  # _template is ignored


def test_progressive_disclosure_levels(tmp_path):
    make_skill(tmp_path, "fees", "fees", "Fee questions", "Read fees.yaml", {"fees.yaml": "a: 1"})
    reg = SkillRegistry(tmp_path)
    reg.reload()
    assert reg.index() == "- fees: Fee questions"  # level 1
    assert "fees.yaml" in reg.load("fees")  # level 2
    assert reg.read_file("fees", "fees.yaml") == "a: 1"  # level 3


@pytest.mark.parametrize("filename", ["../SKILL.md", "..\\..\\.env", "/etc/passwd", "SKILL.md"])
def test_read_file_rejects_anything_not_registered(tmp_path, filename):
    make_skill(tmp_path, "fees", "fees", files={"fees.yaml": "a: 1"})
    reg = SkillRegistry(tmp_path)
    reg.reload()
    with pytest.raises(SkillError):
        reg.read_file("fees", filename)


def test_unknown_skill_rejected(tmp_path):
    reg = SkillRegistry(tmp_path)
    reg.reload()
    with pytest.raises(SkillError):
        reg.load("../secrets")


def test_invalid_skills_are_reported_not_loaded(tmp_path):
    make_skill(tmp_path, "bad", "Bad Name!")
    make_skill(tmp_path, "nodesc", "nodesc", description="''")
    reg = SkillRegistry(tmp_path)
    reg.reload()
    assert reg.names() == []
    assert len(reg.errors) == 2


def test_disallowed_file_types_are_not_exposed(tmp_path):
    make_skill(tmp_path, "s", "s", files={"notes.md": "ok", "run.py": "print(1)"})
    reg = SkillRegistry(tmp_path)
    reg.reload()
    assert set(reg.get("s").files) == {"notes.md"}


def test_frontmatter_required():
    with pytest.raises(SkillError):
        parse_skill_md("no frontmatter here")
