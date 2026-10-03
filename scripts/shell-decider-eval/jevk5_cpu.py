"""Use JevK5's official prompt and scoring with the existing CPU llama.cpp library.

Only the HTTP log-probability transport is replaced. All option logits are read
directly, avoiding the reference HTTP client's approximation for missing top-k tokens.
"""

import time

from jevk5.gguf import JevK5GGUF
from jevk5.prompt import LETTERS
from llama_cpp import Llama, llama_get_logits_ith


class JevK5CPU(JevK5GGUF):
    def __init__(self, checkpoint, **options):
        super().__init__(temperature=1.22, knockout_temperature=0.93)
        self.llama = Llama(model_path=checkpoint, verbose=False, **options)
        self.letter_ids = {}
        for letter in LETTERS:
            tokens = self.llama.tokenize(letter.encode(), add_bos=False)
            assert len(tokens) == 1 and self.llama.detokenize(tokens).decode() == letter
            self.letter_ids[letter] = tokens[0]

    def _logprobs(self, prompt):
        started = time.perf_counter()
        tokens = self.llama.tokenize(prompt.encode(), add_bos=False, special=True)
        if len(tokens) >= self.llama.n_ctx():
            raise ValueError("Decision prompt exceeds the evaluation context limit")
        self.llama.reset()
        self.llama.eval(tokens)
        # llama-cpp-python does not populate Llama.scores with logits_all=False.
        scores = llama_get_logits_ith(self.llama.ctx, -1)
        values = {letter: float(scores[token]) for letter, token in self.letter_ids.items()}
        assert len(set(values.values())) > 1, "Unpopulated option logits"
        # The full-vocabulary log-softmax subtracts a constant; JevK5's option
        # renormalization cancels it, so raw logits produce the same probabilities.
        return values, len(tokens), time.perf_counter() - started

    def system_one(self, state, questions, **_kwargs):
        answer = self.decide(state, questions["effect"])
        return {"answers": {"effect": answer},
                "usage": {"input_tokens": answer["input_tokens"], "output_tokens": 0}}
