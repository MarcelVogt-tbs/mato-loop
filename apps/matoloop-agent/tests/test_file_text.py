import unittest

from matoloop_agent.errors import AgentError
from matoloop_agent.file_text import apply_hunks, parse_patch


class PatchTextTests(unittest.TestCase):
    def apply(self, content, body):
        operations = parse_patch(
            f"*** Begin Patch\n*** Update File: /file\n{body}\n*** End Patch",
            max_bytes=4096,
            max_entries=1,
            validate_path=lambda path: None,
        )
        return apply_hunks(content, operations[0].hunks)

    def test_update_forms_and_line_endings(self):
        cases = [
            ("old\n", "-old\n+new", "new\n"),
            ("old", "@@\n-old\n+new\n*** End of File", "new"),
            ("same\nmiddle\nsame\n", "@@\n-same\n+last\n*** End of File", "same\nmiddle\nlast\n"),
            ("", "@@\n+first", "first\n"),
            ("old", "@@\n+last", "old\nlast\n"),
            ("old\r\n", "@@\n+last\n*** End of File", "old\r\nlast\r\n"),
            ("a\nb\nc\n", "@@\n+last\n@@\n a\n-b\n-c\n+B", "a\nB\nlast\n"),
            ("a\nb\nc\n", "@@ a\n-b\n+B\n@@\n-c\n+C", "a\nB\nC\n"),
            ("a\n\n", "@@\n-\n+last\n*** End of File", "a\nlast\n"),
            ("a\r\n", "@@\n-a\n+A", "A\r\n"),
        ]
        for content, body, expected in cases:
            with self.subTest(content=content, body=body):
                self.assertEqual(self.apply(content, body), expected)

    def test_ambiguous_or_invalid_end_context_is_rejected(self):
        cases = [
            ("same\nsame\n", "@@\n-same\n+changed"),
            ("a\nb\n", "@@\n-a\n+A\n*** End of File"),
            ("a\n", "@@\n-a\n+A\n*** End of File\n@@\n+later"),
        ]
        for content, body in cases:
            with self.subTest(body=body), self.assertRaises(AgentError):
                self.apply(content, body)
