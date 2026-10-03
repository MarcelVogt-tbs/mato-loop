"""Cover Lemonade's actual logprob shape on the text-completions endpoint."""

import unittest
from unittest.mock import Mock

from lemonade import MODEL, LemonadeJevK5


class LemonadeReadoutTests(unittest.TestCase):
    def test_reads_content_logprobs_from_text_completion(self):
        model = LemonadeJevK5("http://unused")
        model.request = Mock(side_effect=[
            {"tokens": [1, 2]},
            {"model": MODEL, "usage": {"prompt_tokens": 2}, "timings": {"cache_n": 0},
             "choices": [{"logprobs": {"content": [{"top_logprobs": [
                 {"token": "A", "logprob": -0.2},
                 {"token": "B", "logprob": -2.0},
                 {"token": "C", "logprob": -3.0},
             ]}]}}]},
        ])
        seen, _, _ = model._logprobs("prompt")
        self.assertEqual(seen, {"A": -0.2, "B": -2.0, "C": -3.0})
