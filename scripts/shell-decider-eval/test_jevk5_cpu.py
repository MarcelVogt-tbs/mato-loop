"""Regression for the initially unpopulated llama.cpp Python score buffer."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jevk5_cpu import JevK5CPU


class ReadoutTests(unittest.TestCase):
    def test_reads_native_logits_instead_of_unpopulated_python_scores(self):
        model = object.__new__(JevK5CPU)
        model.letter_ids = {"A": 0, "B": 1, "C": 2}
        model.llama = SimpleNamespace(
            tokenize=Mock(return_value=[10, 11]),
            n_ctx=lambda: 4096,
            reset=Mock(),
            eval=Mock(),
            ctx=object(),
            scores=[[0.0, 0.0, 0.0]],
        )
        with patch("jevk5_cpu.llama_get_logits_ith", return_value=[2.0, -1.0, 0.5]):
            values, _, _ = model._logprobs("prompt")
        self.assertEqual(values, {"A": 2.0, "B": -1.0, "C": 0.5})
