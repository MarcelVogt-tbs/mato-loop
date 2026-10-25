import unittest
from uuid import uuid4

from pydantic import ValidationError

from matoloop_agent.browser_contracts import BrowserNavigationArguments


class NavigationContractTests(unittest.TestCase):
    def test_strict_host_contract(self):
        browser_id, tab_id = str(uuid4()), str(uuid4())
        for action in (
            "state",
            "new",
            "navigate",
            "select",
            "close",
            "back",
            "forward",
            "reload",
            "stop",
        ):
            fields = {} if action in {"state", "new"} else {"tabId": tab_id}
            if action == "navigate":
                fields["url"] = "about:blank"
            args = {"browserId": browser_id, "action": action, **fields}
            BrowserNavigationArguments.model_validate(args)
            with self.assertRaises(ValidationError):
                BrowserNavigationArguments.model_validate({**args, "javascript": "x"})
        for fields in (
            {"action": "state", "tabId": None},
            {"action": "chrome"},
            {"action": "select"},
            *(
                {"action": "new", "url": url}
                for url in (
                    "javascript:alert(1)",
                    "file:///etc/passwd",
                    "chrome://settings",
                    "about:blank#x",
                    "https://user:secret@example.test",
                    "https://example.test:bad",
                    "https://example.test\\@evil.test",
                    " https://example.test",
                    "https://a\nb.test",
                )
            ),
        ):
            with self.assertRaises(ValidationError):
                BrowserNavigationArguments.model_validate({"browserId": browser_id, **fields})
