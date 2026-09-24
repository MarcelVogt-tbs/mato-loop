_FILE_ROOT_FIELDS = frozenset(
    {"root", "source_root", "destination_root", "sourceRoot", "destinationRoot"}
)
_INTERNAL_FILE_ROOTS = {"files": "session", "computer": "workspace"}
_MODEL_FILE_ROOTS = {value: key for key, value in _INTERNAL_FILE_ROOTS.items()}


def _translate_file_roots(value: object, roots: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {
            key: (
                roots.get(item, item)
                if key in _FILE_ROOT_FIELDS and isinstance(item, str)
                else _translate_file_roots(item, roots)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_translate_file_roots(item, roots) for item in value]
    return value


def internal_file_roots(value: object) -> object:
    return _translate_file_roots(value, _INTERNAL_FILE_ROOTS)


def model_file_roots(value: object) -> object:
    return _translate_file_roots(value, _MODEL_FILE_ROOTS)


def model_file_root_schema(value: object) -> None:
    if isinstance(value, dict):
        enum = value.get("enum")
        if (
            isinstance(enum, list)
            and len(enum) == 2
            and all(isinstance(item, str) for item in enum)
            and set(enum) == {"session", "workspace"}
        ):
            value["enum"] = [_MODEL_FILE_ROOTS[item] for item in enum]
        for item in value.values():
            model_file_root_schema(item)
    elif isinstance(value, list):
        for item in value:
            model_file_root_schema(item)
