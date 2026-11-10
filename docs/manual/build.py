"""Builds the site's docs, site/docs/, from the Markdown pages next to this file and from the help of jaynes's own
commands:

    uv run python docs/manual/build.py

tests/test_site_docs.py builds them again and compares, so the site never says what these pages or a command's
help no longer say, and a new command fails the test until it has a place on the Commands page.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "site" / "docs"
PAGES = ["index", "runs", "roles", "packages", "write-a-role", "commands"]  # in the sidebar's order
GROUPS = [
    ("The page", ["review", "demo", "update", "version"]),
    ("Runs", ["run", "plan", "queue", "answer", "ask", "wait", "output", "delivered", "correct", "halt"]),
    ("Roles and packages", ["roles", "check", "add", "remove", "packages"]),
    ("Your agents' history and the action gate", ["history", "simulate", "autonomy", "policy", "parity"]),
    ("What Claude Code and the plugin run", ["skill", "guide", "mcp", "answered", "hook"]),
]
LOGO = ('<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="3" fill="#6fb4ff"/><circle cx="15" '
        'cy="5" r="2.2" fill="#eef2f7"/><circle cx="15" cy="15" r="2.2" fill="#eef2f7"/><path d="M7.5 9 13 5.8M7.5 11 '
        '13 14.2" stroke="#6fb4ff" stroke-width="1.4"/></svg>')
ICON = ("data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 32 32%22%3E%3Crect "
        "width=%2232%22 height=%2232%22 rx=%227%22 fill=%22%2307090d%22/%3E%3Cpath d=%22M11.5 14.6 20 9.6M11.5 17.4 20 "
        "22.4%22 stroke=%22%236fb4ff%22 stroke-width=%222%22/%3E%3Ccircle cx=%229%22 cy=%2216%22 r=%224.2%22 "
        "fill=%22%236fb4ff%22/%3E%3Ccircle cx=%2222.5%22 cy=%228.5%22 r=%223.2%22 fill=%22%23eef2f7%22/%3E%3Ccircle "
        "cx=%2222.5%22 cy=%2223.5%22 r=%223.2%22 fill=%22%23eef2f7%22/%3E%3C/svg%3E")
FONTS = ("https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500"
         "&display=swap")

md = MarkdownIt("commonmark").enable("table")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>", "", html.unescape(text)).lower()).strip("-")


def anchored(body: str) -> str:
    """Each h2 and h3 with an id from its text, and links between the Markdown pages as links between the pages."""
    body = re.sub(r"<(h[23])>(.*?)</\1>", lambda m: f'<{m[1]} id="{slug(m[2])}">{m[2]}</{m[1]}>', body)
    return re.sub(r'href="([a-z-]+)\.md(#[^"]*)?"', lambda m: f'href="{m[1]}.html{m[2] or ""}"', body)


def commands() -> tuple[str, str]:
    """The Commands page's title and body, from the parser jaynes itself uses."""
    sys.path.insert(0, str(ROOT / "src"))
    from jaynes.cli import parser
    ap = parser()
    sub = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))  # pyright: ignore
    found: dict[str, argparse.ArgumentParser] = dict(sub.choices)  # pyright: ignore
    helps = {a.dest: a.help or "" for a in sub._choices_actions}  # pyright: ignore
    placed = [name for _, names in GROUPS for name in names]
    if sorted(placed) != sorted(found) or len(placed) != len(set(placed)):
        missing, extra = sorted(set(found) - set(placed)), sorted(set(placed) - set(found))
        raise SystemExit(f"GROUPS in {__file__} must place every command once: missing {missing}, unknown {extra}")
    out = ['<h1>Commands</h1><p class="lede">Your agent runs most of these; you need few of them. '
           '<code>jaynes</code> alone opens the page, starting it if it is not running. Every command '
           'also takes <code>--help</code>, and those that keep state take <code>--home</code>, the folder '
           'Jaynes keeps it in: <code>JAYNES_HOME</code>, or <code>~/.jaynes</code>.</p>']
    out.append('<nav class="toc">' + "".join(
        f'<a href="#{slug(title)}">{html.escape(title)}</a>' for title, _ in GROUPS) + "</nav>")
    for title, names in GROUPS:
        out.append(f'<h2 id="{slug(title)}">{html.escape(title)}</h2>')
        for name in names:
            p = found[name]
            usage = re.sub(r"\s+", " ", p.format_usage().removeprefix("usage: ").strip())
            rows: list[str] = []
            for a in p._actions:  # pyright: ignore
                if isinstance(a, argparse._HelpAction) or a.help == argparse.SUPPRESS or "--home" in a.option_strings:  # pyright: ignore
                    continue
                shown = ", ".join(a.option_strings) or a.dest
                if a.choices and not a.option_strings:
                    shown = " | ".join(str(c) for c in a.choices)
                text = html.escape(a.help or "")
                if a.default not in (None, False, [], argparse.SUPPRESS) and a.option_strings:
                    text += f' <span class="default">default {html.escape(str(a.default))}</span>'
                rows.append(f"<dt><code>{html.escape(shown)}</code></dt><dd>{text}</dd>")
            out.append(f'<section class="command"><h3 id="{name}"><code>jaynes {name}</code></h3>'
                       f"<p>{html.escape(helps.get(name, '')[:1].upper() + helps.get(name, '')[1:])}.</p>"
                       f"<pre>{html.escape(usage)}</pre>"
                       + (f'<dl>{"".join(rows)}</dl>' if rows else "") + "</section>")
    return "Commands", "".join(out)


def page(name: str, title: str, body: str, titles: dict[str, str]) -> str:
    links = "".join(f'<a href="{n}.html"{" aria-current=\"page\"" if n == name else ""}>{html.escape(titles[n])}</a>'
                    for n in PAGES)
    i = PAGES.index(name)
    prev = f'<a href="{PAGES[i - 1]}.html">&larr; {html.escape(titles[PAGES[i - 1]])}</a>' if i else "<span></span>"
    nxt = (f'<a href="{PAGES[i + 1]}.html">{html.escape(titles[PAGES[i + 1]])} &rarr;</a>'
           if i + 1 < len(PAGES) else "<span></span>")
    first = re.search(r"<p[^>]*>(.*?)</p>", body, re.S)
    description = html.escape(re.sub(r"<[^>]+>", "", first[1]) if first else title, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(title)} · Jaynes docs</title>
<link rel="icon" href="{ICON}">
<meta name="description" content="{description}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="docs.css">
</head>
<body>
<header class="bar"><div class="wrap">
<a class="logo" href="../index.html">{LOGO}Jaynes <span>docs</span></a>
<nav aria-label="Site"><a href="../index.html">Home</a><a href="../learn.html">Learn</a><a href="index.html">Docs</a><a href="https://github.com/alk0x1/jaynes">GitHub</a></nav>
</div></header>
<div class="wrap docs">
<aside><nav aria-label="Docs">{links}</nav></aside>
<main><article>
{body}
</article>
<footer class="pager">{prev}{nxt}</footer>
</main>
</div>
</body>
</html>
"""


def build() -> dict[str, str]:
    """Every file of site/docs/ by its name, as it must be."""
    titles: dict[str, str] = {}
    bodies: dict[str, str] = {}
    for name in PAGES:
        if name == "commands":
            titles[name], bodies[name] = commands()
            continue
        text = (HERE / f"{name}.md").read_text()
        heading = re.match(r"# (.+)\n", text)
        if heading is None:
            raise SystemExit(f"{name}.md must start with its title, as # Title")
        titles[name] = heading[1]
        body = anchored(md.render(text))
        bodies[name] = re.sub(r"<h1>(.*?)</h1>\s*<p>", r'<h1>\1</h1>\n<p class="lede">', body, count=1)
    files = {f"{n}.html": page(n, titles[n], bodies[n], titles) for n in PAGES}
    files["docs.css"] = (HERE / "docs.css").read_text()
    return files


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for file, text in build().items():
        (OUT / file).write_text(text)
    print(f"built {len(PAGES)} pages in {OUT}")
