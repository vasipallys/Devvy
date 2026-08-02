import asyncio
import logging
from collections.abc import AsyncIterator
from queue import Empty
from threading import Lock

from backend.config import Settings

logger = logging.getLogger(__name__)


class GemmaRuntime:
    """Lazy, process-wide local Gemma runtime with serialized GPU access."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pipeline = None
        self._load_lock = Lock()
        self._generation_lock = asyncio.Lock()
        self.load_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._pipeline is not None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        with self._load_lock:
            if self._pipeline is not None:
                return self._pipeline
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, pipeline

                if self.settings.cpu_threads > 0:
                    torch.set_num_threads(self.settings.cpu_threads)
                kwargs: dict = {"device_map": self.settings.model_device}
                if self.settings.model_dtype != "auto":
                    kwargs["dtype"] = getattr(torch, self.settings.model_dtype)
                if self.settings.model_quantization in {"4bit", "8bit"}:
                    kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=self.settings.model_quantization == "4bit",
                        load_in_8bit=self.settings.model_quantization == "8bit",
                    )
                tokenizer = AutoTokenizer.from_pretrained(
                    self.settings.model_id, token=self.settings.hf_token
                )
                model = AutoModelForCausalLM.from_pretrained(
                    self.settings.model_id, token=self.settings.hf_token, **kwargs
                )
                self._pipeline = pipeline("text-generation", model=model, tokenizer=tokenizer)
                return self._pipeline
            except Exception as exc:
                raw_error = str(exc)
                if "gated repo" in raw_error.lower() or "403 client error" in raw_error.lower():
                    message = (
                        f"Access to {self.settings.model_id} is not authorized. Open the model page "
                        "on Hugging Face, accept Google's Gemma license using the same account as "
                        "HF_TOKEN, and then restart the backend."
                    )
                elif not self.settings.hf_token:
                    message = "HF_TOKEN is missing. Add a Hugging Face read token to .env."
                else:
                    message = (
                        f"Gemma could not load: {raw_error}. Check the model settings and available RAM."
                    )
                self.load_error = message
                logger.exception("Could not load %s", self.settings.model_id)
                raise RuntimeError(message) from exc

    def _generate(
        self,
        messages: list[dict[str, str]],
        streamer=None,
        max_new_tokens: int | None = None,
    ) -> str:
        from opentelemetry import trace

        tracer = trace.get_tracer("gemma-studio.model")
        with tracer.start_as_current_span("gemma.generate") as span:
            span.set_attribute("gen_ai.system", "huggingface")
            span.set_attribute("gen_ai.request.model", self.settings.model_id)
            span.set_attribute("gen_ai.request.max_tokens", self.settings.max_new_tokens)
            span.set_attribute("gen_ai.request.temperature", self.settings.temperature)
            pipe = self._load()
            sampling = self.settings.temperature > 0
            generation_options = {
                "max_new_tokens": max_new_tokens or self.settings.max_new_tokens,
                "do_sample": sampling,
                "return_full_text": False,
                "streamer": streamer,
            }
            if sampling:
                generation_options["temperature"] = max(self.settings.temperature, 0.01)
            else:
                # Override sampling values inherited from the model's generation config.
                generation_options.update({"temperature": None, "top_p": None, "top_k": None})
            result = pipe(messages, **generation_options)
        generated = result[0]["generated_text"]
        if isinstance(generated, list):
            return generated[-1]["content"]
        return str(generated)

    async def generate(
        self,
        messages: list[dict[str, str]],
        token_queue: asyncio.Queue[str] | None = None,
        max_new_tokens: int | None = None,
    ) -> str:
        chunks: list[str] = []
        async for token in self.stream(messages, max_new_tokens=max_new_tokens):
            chunks.append(token)
            if token_queue is not None:
                await token_queue.put(token)
        return "".join(chunks)

    async def stream(
        self,
        messages: list[dict[str, str]],
        max_new_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        from transformers import TextIteratorStreamer

        pipe = await asyncio.to_thread(self._load)
        streamer = TextIteratorStreamer(
            pipe.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=1
        )
        async with self._generation_lock:
            task = asyncio.create_task(
                asyncio.to_thread(self._generate, messages, streamer, max_new_tokens)
            )
            iterator = iter(streamer)
            while True:
                try:
                    token = await asyncio.to_thread(next, iterator, None)
                except Empty:
                    # CPU prompt prefill can take minutes for documents. A streamer
                    # timeout only means that no token is ready yet.
                    if task.done():
                        await task  # Re-raise the actual model exception, if any.
                        break
                    continue
                if token is None:
                    break
                yield token
            await task
