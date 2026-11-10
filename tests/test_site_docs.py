"""The site's docs say what their Markdown pages and jaynes's own commands say, and no link on the site leads
nowhere."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def build() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("docs_build", ROOT / "docs" / "manual" / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build()


def test_the_docs_on_the_site_are_built_from_the_pages_and_the_commands_as_they_are_now() -> None:
    built = build()
    stale = [name for name, text in built.items() if not (SITE / "docs" / name).is_file()
             or (SITE / "docs" / name).read_text() != text]
    assert not stale, f"run: uv run python docs/manual/build.py ({', '.join(stale)} changed)"
    from jaynes.cli import parser
    commands = built["commands.html"]
    for name in parser().format_usage().split("{", 1)[1].split("}", 1)[0].split(","):
        assert f'id="{name}"' in commands, name


def test_every_link_on_the_site_leads_to_a_page_and_a_place_in_it() -> None:
    pages = {p.relative_to(SITE).as_posix(): p.read_text() for p in SITE.rglob("*.html")}
    ids = {name: set(re.findall(r'id="([^"]+)"', text)) for name, text in pages.items()}
    broken: list[str] = []
    for name, text in pages.items():
        for href in re.findall(r'href="([^"]+)"', text):
            if re.match(r"(https?:|mailto:|data:)", href):
                continue
            path, _, anchor = href.partition("#")
            target = (Path(name).parent / path).as_posix() if path else name
            target = re.sub(r"[^/]+/\.\./", "", target)
            if target.endswith("/") or target == "docs":  # a folder serves its index.html
                target = target.rstrip("/") + "/index.html"
            if target.endswith(".css"):
                exists = (SITE / target).is_file()
            else:
                exists = target in pages and (not anchor or anchor == "top" or anchor in ids[target])
            if not exists:
                broken.append(f"{name}: {href}")
    assert not broken, broken
