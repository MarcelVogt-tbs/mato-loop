import unittest

from matoloop_agent.protocol import Hello, PathArguments
from matoloop_agent.windows_paths import absolute, native


class WindowsPathTests(unittest.TestCase):
    def test_drive_paths_share_one_wire_form(self) -> None:
        self.assertEqual(absolute("C:/Users/Test"), "C:/Users/Test")
        self.assertEqual(PathArguments(path="C:/Users/Test").path, "C:/Users/Test")
        for invalid in (
            "c:/x",
            "C:x",
            "C:/x/../y",
            "C:/x//y",
            "C:/x:stream",
            "C:/COM1",
            "//server/share",
            "C:\\Users",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                absolute(invalid)
        with self.assertRaises(ValueError):
            native("/tmp")

    def test_hello_accepts_a_windows_home(self) -> None:
        hello = Hello(
            agentId="2ea504cc-e3c6-4e0d-aa05-080328100fe8",
            agentVersion="0.1.0",
            computerName="windows",
            platform="win32",
            homeDirectory="C:/Users/Test/workspace",
            operations=["shell.run"],
        )
        self.assertEqual(hello.home_directory, "C:/Users/Test/workspace")
