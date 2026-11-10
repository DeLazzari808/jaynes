"""The Chrome Jaynes drives: it walks a screen with the closed vocabulary and lets only the product's own GETs out."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from jaynes.browser import open_page, route_matches
from tests.needs import chrome_runs

PRODUCT = Path(__file__).parent / "product"
needs_chrome = chrome_runs


def served_product(root: Path) -> tuple[ThreadingHTTPServer, list[tuple[str, str]]]:
    """The invented product served from ``root``, with every request it receives recorded."""
    seen: list[tuple[str, str]] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def do_GET(self) -> None:
            seen.append(("GET", self.path))
            super().do_GET()

        def do_POST(self) -> None:
            seen.append(("POST", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


@pytest.fixture
def product(tmp_path: Path) -> Iterator[tuple[str, list[tuple[str, str]]]]:
    shutil.copytree(PRODUCT / "app", tmp_path / "site")
    shutil.copytree(PRODUCT / "data", tmp_path / "site" / "data")
    server, seen = served_product(tmp_path / "site")
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


@needs_chrome
def test_a_walk_blocks_every_post_the_listed_gets_and_other_origins(
        product: tuple[str, list[tuple[str, str]]], tmp_path: Path) -> None:
    origin, seen = product
    with open_page(origin, deny=["/api/covers"]) as page:
        page.view("dark")
        assert page.act({"action": "open", "to": "/index.html"}) == ""
        assert page.act({"action": "clickText", "text": "Paper Lanterns"}) == ""
        assert page.act({"action": "expectRoute", "route": "/index.html#/books/<id>"}) == ""
        assert page.act({"action": "expectText", "text": "Picked by Tomas"}) == ""
        assert page.act({"action": "pressButton", "name": "Save note"}) == ""
        page.settle()
        shot = page.shot(tmp_path / "M1-s01.png")
    assert ("POST", "/api/notes") not in seen and not any(p.startswith("/api/covers") for _, p in seen)
    assert {"method": "POST", "path": "/api/notes"} in page.blocked
    assert {"method": "GET", "path": "/api/covers/refresh"} in page.blocked
    assert {"method": "GET", "path": "<other origin: example.com>"} in page.blocked
    assert ("GET", "/data/books.json") in seen
    assert (tmp_path / "M1-s01.png").read_bytes()[:4] == b"\x89PNG" and "Picked by Tomas" in shot.text
    assert any(c["name"] == "Save note" for c in shot.controls)


@needs_chrome
def test_what_cannot_be_done_is_said_not_raised(product: tuple[str, list[tuple[str, str]]]) -> None:
    origin, _ = product
    with open_page(origin) as page:
        page.view("light")
        page.act({"action": "open", "to": "/index.html"})
        assert page.act({"action": "clickText", "text": "A book nobody picked"}) == \
            "nothing visible to clickText with 'A book nobody picked'"
        assert "does not appear" in page.act({"action": "expectText", "text": "Picked by Tomas"})
        assert page.act({"action": "type", "field": "Search books", "text": "orchard"}) == ""
        assert "A Winter Orchard" in page.text() and "The Salt Road" not in page.text()
        assert page.act({"action": "open", "to": "https://example.com/"}).startswith("open failed")


@needs_chrome
def test_a_recording_page_keeps_the_products_json_responses(product: tuple[str, list[tuple[str, str]]]) -> None:
    origin, _ = product
    with open_page(origin, record=True) as page:
        page.view("dark")
        page.act({"action": "open", "to": "/index.html"})
    assert '"The Salt Road"' in page.responses["/data/books.json"]["body"]


def test_a_route_names_any_one_segment_with_angle_brackets() -> None:
    assert route_matches("/books/<id>", "/books/b2?tab=notes")
    assert not route_matches("/books/<id>", "/books/b2/edit")
    assert route_matches("/?view=books&id=<id>", "/?view=books&id=b2")
    assert not route_matches("/?view=books&id=<id>", "/?view=people&id=b2")
