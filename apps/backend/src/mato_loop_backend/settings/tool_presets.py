from mato_loop_backend.settings.models import ToolExecutionPolicy, ToolPermissionPreset
from mato_loop_backend.tools.actions import ToolActionCategory


def preset_policy(preset: ToolPermissionPreset, category: ToolActionCategory) -> ToolExecutionPolicy:
    if category == "read_sensitive":
        ask = preset in {"ask_all", "ask_changes_sensitive"}
    elif category == "delete":
        ask = preset != "auto_all"
    elif category == "write":
        ask = preset in {"ask_all", "ask_changes_sensitive", "ask_changes"}
    else:
        ask = preset == "ask_all"
    return ToolExecutionPolicy.ASK if ask else ToolExecutionPolicy.AUTO
