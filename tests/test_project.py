"""A project named with only digits (e.g. `project new 2`) used to crash every
command after creation: the config coerced "2" to int 2, and `projects_dir / 2`
raised `TypeError: unsupported operand type(s) for /: 'PosixPath' and 'int'`."""

from hermes.config import Config
from hermes.project import Project


def test_config_set_can_skip_coercion():
    c = Config({"current_project": ""})
    c.set("current_project", "2")            # default: coerced — this is the trap
    assert c.get("current_project") == 2
    c.set("current_project", "2", coerce=False)  # what the project commands now do
    assert c.get("current_project") == "2"


def test_numeric_project_name_creates_and_loads(tmp_path):
    p = Project.create(tmp_path, "2")
    assert p.name == "2"
    assert p.mission_path.exists()
    # Loading with an int (as a coerced config value would hand us) must not crash.
    loaded = Project.load(tmp_path, 2)
    assert loaded.name == "2"
    assert loaded.mission_path.name == "mission.md"
