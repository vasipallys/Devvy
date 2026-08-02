import asyncio
import ipaddress
import re
import socket
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from backend.config import Settings


_image_pipeline = None
_image_pipeline_model: str | None = None
_image_pipeline_lock = threading.Lock()
_image_generation_lock = asyncio.Lock()


def extract_document(path: Path, max_chars: int = 80_000) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        from docx import Document

        text = "\n".join(p.text for p in Document(path).paragraphs)
    elif suffix in {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported document type: {suffix}")
    return text[:max_chars]


async def web_search(query: str, limit: int = 5) -> list[dict]:
    """API-key-free search and retrieval using local Python libraries.

    DDGS discovers sources; HTTPX and BeautifulSoup retrieve and clean their content.
    The network is still required because the underlying sources are public websites.
    """
    from ddgs import DDGS

    lowered = query.lower()
    freshness = "m" if any(
        term in lowered
        for term in ("latest", "today", "current", "recent", "news", "this week", "this month")
    ) else None
    raw = await asyncio.wait_for(
        asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=limit, timelimit=freshness))
        ),
        timeout=30,
    )

    async def enrich(item: dict) -> dict:
        url = item.get("href") or item.get("url") or ""
        result = {
            "title": item.get("title") or "Search result",
            "url": url,
            "content": item.get("body") or item.get("content") or "",
        }
        if url:
            try:
                page = await fetch_page(url)
                if page:
                    result["content"] = page
            except Exception:
                # Search snippets remain useful when a site blocks automated retrieval.
                pass
        return result

    return list(await asyncio.gather(*(enrich(item) for item in raw)))


async def _validate_public_url(url: str) -> None:
    if not re.match(r"^https?://", url):
        raise ValueError("Only http(s) URLs are allowed")
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, None)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Private and local network addresses are not allowed")


async def fetch_page(url: str) -> str:
    current = url
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=False, headers={"User-Agent": "GemmaStudio/0.1"}
    ) as client:
        for _ in range(6):
            await _validate_public_url(current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response did not include a destination")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return ""
                declared = response.headers.get("content-length")
                if declared and int(declared) > 2 * 1024 * 1024:
                    raise ValueError("Research page exceeds the 2 MB retrieval limit")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        raise ValueError("Research page exceeds the 2 MB retrieval limit")
                text = content.decode(response.encoding or "utf-8", errors="replace")
                break
        else:
            raise ValueError("Research page exceeded the redirect limit")
    soup = BeautifulSoup(text, "html.parser")
    for node in soup(["script", "style", "nav", "footer"]):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:3_000]


async def generate_image(prompt: str, settings: Settings) -> str:
    if not settings.image_model_id:
        raise RuntimeError("Image generation is disabled. Set IMAGE_MODEL_ID in .env.")

    def run() -> str:
        global _image_pipeline, _image_pipeline_model

        import torch
        from diffusers import AutoPipelineForText2Image

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        with _image_pipeline_lock:
            if _image_pipeline is None or _image_pipeline_model != settings.image_model_id:
                pipe = AutoPipelineForText2Image.from_pretrained(
                    settings.image_model_id, torch_dtype=dtype
                )
                pipe = pipe.to(device)
                if device == "cpu":
                    pipe.enable_attention_slicing()
                pipe.set_progress_bar_config(disable=True)
                _image_pipeline = pipe
                _image_pipeline_model = settings.image_model_id
            pipe = _image_pipeline
        image = pipe(
            prompt=prompt,
            num_inference_steps=max(1, min(settings.image_inference_steps, 50)),
        ).images[0]
        name = f"{uuid4()}.png"
        image.save(settings.generated_dir / name)
        return f"/generated/{name}"

    # Diffusers pipelines are not safe to invoke concurrently. Serialization also
    # prevents two CPU generations from exhausting system memory.
    async with _image_generation_lock:
        return await asyncio.to_thread(run)


def research_context(results: list[dict]) -> str:
    return "\n".join(
        f"[{index}] {item.get('title', 'Result')}\nURL: {item.get('url') or item.get('href')}\n"
        f"Summary: {item.get('content') or item.get('body', '')}"
        for index, item in enumerate(results, 1)
    )
