import pytest

from backend.tools import fetch_page, research_context


async def test_fetch_page_blocks_local_network():
    with pytest.raises(ValueError, match="Private and local"):
        await fetch_page("http://127.0.0.1:6006")


def test_research_context_contains_citation_data():
    context = research_context(
        [{"title": "Example", "url": "https://example.com", "content": "Evidence"}]
    )
    assert "[1] Example" in context
    assert "URL: https://example.com" in context
    assert "Evidence" in context
