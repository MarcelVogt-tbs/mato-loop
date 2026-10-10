from mato_loop_backend.settings.models import SkillReference
from mato_loop_backend.skill_packages import SkillFile, SkillPackage, skill_md
from mato_loop_backend.skill_storage import SkillStorage


def store_skill(skill, owner_id=None):
    package = SkillPackage({"SKILL.md": SkillFile(skill_md(skill.name, skill.description, skill.instructions))})
    skill.revision = SkillStorage().publish(owner_id, skill.id, package)
    skill.document = package.files["SKILL.md"].content.decode()
    skill.owner_id = owner_id
    skill.resource_paths = sorted(package.files)
    return SkillReference(id=skill.id, name=skill.name, revision=skill.revision, enabled=skill.enabled)
