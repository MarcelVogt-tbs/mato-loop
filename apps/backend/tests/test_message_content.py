import pytest

from mato_loop_backend.message_content import canonical_assistant_content


def test_complete_markdown_envelope_is_unwrapped() -> None:
    content = """````markdown
# Document

```mermaid
flowchart LR
A --> B
```
````
"""

    assert canonical_assistant_content(content) == """# Document

```mermaid
flowchart LR
A --> B
```
"""


@pytest.mark.parametrize(
    "content",
    [
        "Before\n```markdown\n# Example\n```",
        "```markdown\n# Unclosed",
        "```markdown\n# Mismatched\n~~~",
        "```python\nprint('markdown')\n```",
        "    ```markdown\n# Indented code\n    ```",
    ],
)
def test_non_envelope_content_is_unchanged(content: str) -> None:
    assert canonical_assistant_content(content) == content
