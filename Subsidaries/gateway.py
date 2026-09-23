"""
ARIA v2.0 — Model Gateway
─────────────────────────
Single interface for all Ollama model interactions.
Every component calls Gateway instead of ollama.chat() directly.
Manages VRAM by controlling keep_alive windows.
"""

import time
import ollama


class ModelGateway:
    """Unified interface for Ollama model calls with VRAM management."""

    def __init__(self, default_model="llama3.2", keep_alive="5m"):
        self.default_model = default_model
        self.keep_alive = keep_alive
        self._last_model = None       # track which model is currently warm
        self._call_count = 0
        self._total_latency = 0.0

    def call(self, model_id, messages, **kwargs):
        """
        Call an Ollama model and return the response content string.

        Args:
            model_id:  Ollama model name (e.g. 'llama3.2', 'qwen3:4b')
            messages:  List of message dicts [{"role": ..., "content": ...}]
            **kwargs:  Optional overrides — keep_alive, temperature, etc.

        Returns:
            str: The model's reply text.

        Raises:
            ModelNotFoundError: If the model isn't pulled locally.
            GatewayError: For other Ollama/network failures.
        """
        model = model_id or self.default_model
        ka = kwargs.pop("keep_alive", self.keep_alive)

        # Log model switch for debugging
        if self._last_model and self._last_model != model:
            print(f"[Gateway] Model switch: {self._last_model} -> {model}")
        self._last_model = model

        t0 = time.time()
        try:
            response = ollama.chat(
                model=model,
                messages=messages,
                keep_alive=ka,
                **kwargs,
            )
            latency = time.time() - t0
            self._call_count += 1
            self._total_latency += latency

            # ollama.chat() returns a Pydantic object
            reply = response.message.content
            tokens_in = response.prompt_eval_count or 0
            tokens_out = response.eval_count or 0

            print(f"[Gateway] {model} -- {latency:.1f}s -- "
                  f"{tokens_in}->{tokens_out} tokens")

            return GatewayResponse(
                content=reply,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency=latency,
                done=response.done,
            )

        except ollama.ResponseError as e:
            if "not found" in str(e).lower():
                print(f"[Gateway] Model '{model}' not found, "
                      f"falling back to {self.default_model}")
                if model != self.default_model:
                    return self.call(self.default_model, messages, keep_alive=ka)
                raise ModelNotFoundError(model) from e
            raise GatewayError(f"Ollama error: {e}") from e

        except Exception as e:
            raise GatewayError(f"Gateway error: {e}") from e

    def list_available(self):
        """Return list of model names currently pulled in Ollama."""
        try:
            result = ollama.list()
            return [m.model for m in result.models]
        except Exception:
            return []

    def is_available(self):
        """Check whether the Ollama service is reachable."""
        try:
            ollama.list()
            return True
        except Exception:
            return False

    def warm_up(self, model_id=None):
        """Pre-load a model into VRAM so the first real query is fast."""
        model = model_id or self.default_model
        try:
            print(f"[Gateway] Warming up {model}...")
            self.call(model, [{"role": "user", "content": "hi"}])
            print(f"[Gateway] {model} warmed up and ready.")
        except Exception as e:
            print(f"[Gateway] Warm-up failed for {model}: {e}")

    def stats(self):
        """Return usage statistics."""
        return {
            "total_calls": self._call_count,
            "total_latency": round(self._total_latency, 2),
            "avg_latency": round(self._total_latency / max(self._call_count, 1), 2),
            "last_model": self._last_model,
        }


class GatewayResponse:
    """Structured response from a Gateway call."""

    def __init__(self, content, model, tokens_in=0, tokens_out=0,
                 latency=0.0, done=True):
        self.content = content
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.latency = latency
        self.done = done

    def to_dict(self):
        return {
            "content": self.content,
            "model": self.model,
            "usage": {
                "input_tokens": self.tokens_in,
                "output_tokens": self.tokens_out,
            },
            "latency": round(self.latency, 2),
            "stop_reason": "stop" if self.done else None,
        }


class ModelNotFoundError(Exception):
    """Raised when a requested model is not pulled locally."""
    def __init__(self, model):
        self.model = model
        super().__init__(f"Model '{model}' not found locally. "
                         f"Run: ollama pull {model}")


class GatewayError(Exception):
    """General Gateway failure."""
    pass
