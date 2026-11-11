"""A headless Chrome that Jaynes drives itself, for steps that look at a running product.

Agents never drive it: they write paths in a closed vocabulary (``ACTIONS``), and Jaynes performs each action with
code of its own. The browser talks to Chrome's debugging protocol over a websocket with the standard library
only. Every request the page makes passes through Jaynes first: it goes on only if it is a GET to the product's own
origin and not one of the GETs the person listed as having effects (``deny``); anything else is refused and
recorded. So walking a screen cannot save, send or fetch from elsewhere, whatever the screen's code tries.

Chrome runs outside Jaynes's box, since macOS opens no box inside another and Chrome needs its own; the product it
looks at runs inside one (``jaynes.screens``).
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

Theme = Literal["dark", "light"]
DESKTOP, PHONE = (1440, 900), (390, 844)
CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium")


def chrome() -> str | None:
    """Google Chrome or Chromium on this machine, ``JAYNES_CHROME`` first."""
    for c in (os.environ.get("JAYNES_CHROME"), *CHROME, shutil.which("google-chrome"), shutil.which("chromium")):
        if c and Path(c).is_file():
            return c
    return None


class BrowserError(Exception):
    pass


class _Socket:
    """The client end of a websocket: enough of RFC 6455 for Chrome's debugging protocol."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        u = urlsplit(url)
        self.sock = socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserError("Chrome closed the debugging connection")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise BrowserError(f"Chrome refused the debugging connection: {head[:200]!r}")

    def send(self, text: str) -> None:
        data = text.encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 1 << 16 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = secrets.token_bytes(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise BrowserError("Chrome closed the debugging connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout: float) -> str | None:
        """The next text message, or None when none arrives within ``timeout`` seconds."""
        self.sock.settimeout(timeout)
        try:
            if not self.buf:
                chunk = self.sock.recv(1 << 16)
                if not chunk:
                    raise BrowserError("Chrome closed the debugging connection")
                self.buf += chunk
        except TimeoutError:
            return None
        self.sock.settimeout(30.0)  # a message has begun: the rest follows
        message = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            n = struct.unpack(">H", self._read(2))[0] if n == 126 else struct.unpack(">Q", self._read(8))[0] \
                if n == 127 else n
            payload = self._read(n)
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise BrowserError("Chrome closed the debugging connection")
            if opcode == 0x9:  # ping
                continue
            message += payload
            if b0 & 0x80:
                return message.decode(errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@dataclass
class Shot:
    file: str
    url: str
    text: str
    controls: list[dict[str, Any]]
    error: str = ""
    region: list[int] = field(default_factory=list[int])  # x, y, width, height: what the last action changed


@dataclass
class Page:
    """One tab of a headless Chrome, with every request guarded."""

    origin: str  # scheme://host:port of the product; nothing else is reached
    deny: tuple[str, ...] = ()  # paths of GETs with effects, matched as a prefix
    record: bool = False  # keep the bodies of the product's JSON responses
    web: bool = False  # a page of the public internet instead: any GET to a public address, nothing else
    blocked: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    responses: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    _ws: _Socket | None = None
    _proc: subprocess.Popen[bytes] | None = None
    _profile: str = ""
    _next: int = 0
    _inflight: set[str] = field(default_factory=set[str])
    _quiet_since: float = 0.0
    _loaded: bool = False
    _meta: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    _finished: list[str] = field(default_factory=list[str])
    _public: dict[str, bool] = field(default_factory=dict[str, bool])

    # lifecycle ----------------------------------------------------------------

    def __enter__(self) -> Page:
        binary = chrome()
        if binary is None:
            raise BrowserError("Jaynes looks at screens with Google Chrome and found none; install it or set "
                               "JAYNES_CHROME")
        self._profile = tempfile.mkdtemp(prefix="jaynes-chrome-")
        # a mock keychain: Chrome never asks for the person's keychain, and does not hang where there is none
        self._proc = subprocess.Popen(
            [binary, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self._profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
             "--mute-audio", "--disable-background-networking", "--disable-component-update", "--use-mock-keychain",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file = Path(self._profile) / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not (port_file.is_file() and port_file.read_text().count("\n") >= 1):
            if self._proc.poll() is not None or time.monotonic() > deadline:
                self.__exit__()
                raise BrowserError("Chrome did not start")
            time.sleep(0.05)
        port = int(port_file.read_text().split("\n")[0])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/json/list")
        tabs = json.loads(conn.getresponse().read())
        conn.close()
        page = next(t for t in tabs if t.get("type") == "page")
        self._ws = _Socket(str(page["webSocketDebuggerUrl"]))
        self.call("Page.enable")
        self.call("Network.enable")
        self.call("Fetch.enable", patterns=[{"urlPattern": "*", "requestStage": "Request"}])
        return self

    def __exit__(self, *_: object) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
        if self._proc is not None:
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            self._proc.wait()
            self._proc = None
        if self._profile:
            shutil.rmtree(self._profile, ignore_errors=True)

    # protocol -----------------------------------------------------------------

    def call(self, method: str, timeout: float = 30.0, **params: Any) -> dict[str, Any]:
        assert self._ws is not None
        self._next += 1
        mine = self._next
        self._ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._ws.recv(max(0.01, deadline - time.monotonic()))
            if msg is None:
                break
            data = json.loads(msg)
            if data.get("id") == mine:
                if "error" in data:
                    raise BrowserError(f"{method}: {data['error'].get('message')}")
                return dict(data.get("result") or {})
            self._event(data)
        raise BrowserError(f"Chrome did not answer {method} within {timeout:.0f} s")

    def _tell(self, method: str, **params: Any) -> None:
        """A command whose answer nobody waits for, such as letting a paused request go on."""
        assert self._ws is not None
        self._next += 1
        self._ws.send(json.dumps({"id": self._next, "method": method, "params": params}))

    def pump(self, seconds: float) -> None:
        assert self._ws is not None
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            msg = self._ws.recv(left)
            if msg is None:
                return
            data = json.loads(msg)
            if "method" in data:
                self._event(data)

    def _event(self, data: dict[str, Any]) -> None:
        method, p = data.get("method"), dict(data.get("params") or {})
        if method == "Fetch.requestPaused":
            req = dict(p["request"])
            url, verb = str(req["url"]), str(req["method"])
            if url.startswith(("data:", "blob:")):
                self._tell("Fetch.continueRequest", requestId=p["requestId"])
                return
            if self.web:
                host = urlsplit(url).hostname or ""
                if host not in self._public:
                    self._public[host] = public(url)
                if verb == "GET" and self._public[host]:
                    self._tell("Fetch.continueRequest", requestId=p["requestId"])
                else:
                    self.blocked.append({"method": verb, "path": f"<not public: {host}>"})
                    self._tell("Fetch.failRequest", requestId=p["requestId"], errorReason="BlockedByClient")
                return
            own = url.startswith(self.origin + "/") or url == self.origin
            path = urlsplit(url).path if own else ""
            if own and verb == "GET" and not any(path == d or path.startswith(d.rstrip("/") + "/") for d in self.deny):
                self._tell("Fetch.continueRequest", requestId=p["requestId"])
            else:
                where = path + (f"?{urlsplit(url).query}" if urlsplit(url).query else "") if own \
                    else f"<other origin: {urlsplit(url).hostname}>"
                self.blocked.append({"method": verb, "path": where})
                self._tell("Fetch.failRequest", requestId=p["requestId"], errorReason="BlockedByClient")
        elif method == "Network.requestWillBeSent":
            self._inflight.add(str(p["requestId"]))
        elif method == "Network.responseReceived":
            r = dict(p["response"])
            self._meta[str(p["requestId"])] = {"url": r.get("url"), "status": r.get("status"),
                                                "mime": r.get("mimeType", "")}
        elif method in ("Network.loadingFinished", "Network.loadingFailed"):
            rid = str(p["requestId"])
            self._inflight.discard(rid)
            if not self._inflight:
                self._quiet_since = time.monotonic()
            if method == "Network.loadingFinished" and self.record:
                self._finished.append(rid)
        elif method == "Page.loadEventFired":
            self._loaded = True

    # what the page does -------------------------------------------------------

    def view(self, theme: str, size: tuple[int, int] = DESKTOP) -> None:
        width, height = size
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                  mobile=width < 600)
        self.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": theme}])

    def goto(self, path: str) -> None:
        if not path.startswith("/"):
            raise BrowserError(f"{path!r} is not a path on the product's own origin")
        self._loaded = False
        self.call("Page.navigate", url=self.origin + path)
        deadline = time.monotonic() + 15
        while not self._loaded and time.monotonic() < deadline:
            self.pump(0.1)
        self.settle()

    def settle(self, quiet: float = 0.5, limit: float = 8.0) -> None:
        """Wait until no request has been in flight for ``quiet`` seconds, at most ``limit`` seconds."""
        start = time.monotonic()
        self._quiet_since = self._quiet_since or start
        while time.monotonic() - start < limit:
            self.pump(0.1)
            if not self._inflight and time.monotonic() - max(self._quiet_since, start) >= quiet:
                break
        if self.record:
            self._bodies()

    def _bodies(self) -> None:
        for rid in self._finished:
            meta = self._meta.get(rid) or {}
            url = str(meta.get("url") or "")
            if not url.startswith(self.origin) or "json" not in str(meta.get("mime", "")):
                continue
            try:
                body = self.call("Network.getResponseBody", requestId=rid)
            except BrowserError:
                continue
            text = base64.b64decode(body["body"]).decode(errors="replace") if body.get("base64Encoded") \
                else str(body.get("body", ""))
            self.responses[url[len(self.origin):]] = {"status": meta.get("status"), "body": text}
        self._finished.clear()

    def evaluate(self, script: str) -> Any:
        out = self.call("Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in out:
            raise BrowserError(str(out["exceptionDetails"].get("text", "script failed")))
        return dict(out.get("result") or {}).get("value")

    def screenshot(self, path: Path) -> None:
        """A PNG, or a JPEG when ``path`` ends in .jpg, which weighs a tenth for a photograph of a busy page."""
        jpeg = path.suffix.lower() in (".jpg", ".jpeg")
        shot = self.call("Page.captureScreenshot", format="jpeg", quality=80) if jpeg else \
            self.call("Page.captureScreenshot", format="png")
        data = shot["data"]
        path.write_bytes(base64.b64decode(data))

    def location(self) -> str:
        """The path, query and fragment the page is at."""
        return str(self.evaluate("location.pathname + location.search + location.hash"))

    def text(self) -> str:
        return str(self.evaluate("document.body ? document.body.innerText : ''") or "")

    def controls(self) -> list[dict[str, Any]]:
        return list(self.evaluate(_CONTROLS) or [])

    def shot(self, path: Path) -> Shot:
        self.screenshot(path)
        return Shot(path.name, self.location(), self.text(), self.controls(), region=self.region())

    def region(self, around: Sequence[int] = ()) -> list[int]:
        """Where to look in the screenshot, as x, y, width and height in its pixels: ``around``, a box in them, else
        what the last action changed, else where the page has content; padded, at least 480 by 270, and never
        cutting through a line or a control."""
        found = self.evaluate(f"({_REGION})({json.dumps(list(around) or None)})")
        box = cast(list[Any], found) if isinstance(found, list) else []
        return [int(x) for x in box] if len(box) == 4 else []

    def difference(self, before: Path, after: Path) -> list[int]:
        """Where two screenshots differ, as x, y, width and height in their pixels; empty when they do not, or
        when this page cannot compare them."""
        urls = [json.dumps("data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()) for p in (before, after)]
        try:
            found = self.evaluate(f"({_DIFFERENCE})({urls[0]}, {urls[1]})")
        except BrowserError:
            return []
        box = cast(list[Any], found) if isinstance(found, list) else []
        return [int(x) for x in box] if len(box) == 4 else []

    def click_at(self, x: float, y: float) -> None:
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def act(self, action: Mapping[str, Any], wait: bool = True) -> str:
        """Perform one action of the vocabulary; returns what went wrong, or empty. ``wait=False`` returns right
        after a click, so a transition can be captured while it runs."""
        kind = str(action.get("action"))
        try:
            self.evaluate(_WATCH)  # what the action changes, for region()
            if kind == "open":
                self.goto(str(action["to"]))
                return ""
            if kind == "back":
                self.evaluate("history.back()")
                self.settle()
                return ""
            if kind == "scroll":
                done = self.evaluate(f"({_SCROLL})({json.dumps(action.get('region', 'page'))}, "
                                     f"{json.dumps(str(action.get('to', 'bottom')))})")
                self.settle(quiet=0.3)
                return "" if done else f"no region {action.get('region')!r} to scroll"
            if kind == "type":
                found = self.evaluate(f"({_FIELD})({json.dumps(str(action['field']))})")
                if not found:
                    return f"no field labelled {action['field']!r}"
                self.call("Input.insertText", text=str(action.get("text", "")))
                self.settle(quiet=0.3)
                return ""
            if kind in ("clickText", "openRow", "pressButton"):
                what = str(action.get("text") or action.get("name") or "")
                at = self.evaluate(f"({_FIND})({json.dumps(kind)}, {json.dumps(what)}, "
                                   f"{json.dumps(bool(action.get('exact')))})")
                if not at:
                    return f"nothing visible to {kind} with {what!r}"
                self.click_at(float(at[0]), float(at[1]))
                if wait:
                    self.settle()
                return ""
            if kind == "expectText":
                return "" if str(action["text"]) in self.text() else f"the text {action['text']!r} does not appear"
            if kind == "expectRoute":
                where = self.location()
                return "" if route_matches(str(action["route"]), where) else \
                    f"the page is at {where}, not at the route {action['route']}"
        except BrowserError as err:
            return f"{kind} failed: {err}"
        return f"{kind!r} is not an action of the vocabulary"


def route_matches(route: str, where: str) -> bool:
    """Whether a page at ``where`` (path, query, fragment) is at ``route``, where ``<name>`` stands for any one
    segment or value. A route without a query or fragment is matched against the path alone."""
    target = where if any(c in route for c in "?#") else re.split(r"[?#]", where, maxsplit=1)[0]
    pattern = "".join("[^/?&#=]+" if re.fullmatch(r"<\w+>", part) else re.escape(part)
                      for part in re.split(r"(<\w+>)", route))
    return re.fullmatch(pattern, target) is not None


def open_page(origin: str, deny: Sequence[str] = (), record: bool = False) -> Page:
    return Page(origin.rstrip("/"), tuple(deny), record)


def public(url: str) -> bool:
    """Whether ``url`` is http or https to a host whose every address is on the public internet: never this
    computer, its network, or a name that does not resolve."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        found = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError):
        return False
    try:
        return bool(found) and all(ipaddress.ip_address(str(a[4][0]).split("%")[0]).is_global for a in found)
    except ValueError:
        return False


def photograph(url: str, out: Path, size: tuple[int, int] = DESKTOP, tallest: int = 2400) -> str:
    """A picture of the public page at ``url`` in ``out``, as tall as its content up to ``tallest``; its title.
    Every request the page makes goes on only to the public internet, so it cannot reach this computer, its
    network, or Jaynes's own page."""
    if not public(url):
        raise BrowserError(f"{url} is not a page on the public internet")
    with Page("", web=True) as page:
        page.view("light", size)
        page._loaded = False  # pyright: ignore[reportPrivateUsage]
        went = page.call("Page.navigate", url=url)
        if went.get("errorText"):
            raise BrowserError(f"{url} did not open: {went['errorText']}")
        deadline = time.monotonic() + 20
        while not page._loaded and time.monotonic() < deadline:  # pyright: ignore[reportPrivateUsage]
            page.pump(0.1)
        page.settle(limit=10.0)
        tall = page.evaluate("Math.max(document.documentElement.scrollHeight, document.body ? "
                             "document.body.scrollHeight : 0)")
        page.view("light", (size[0], min(max(int(tall or 0), size[1]), tallest)))
        page.settle(limit=4.0)
        page.screenshot(out)
        return str(page.evaluate("document.title") or "")


_WATCH = """(() => {
  const changed = window.__jaynesChanged = [];
  if (window.__jaynesWatch) window.__jaynesWatch.disconnect();
  window.__jaynesWatch = new MutationObserver(records => records.forEach(r => {
    const el = r.type === "childList" ? null : (r.target.nodeType === 1 ? r.target : r.target.parentElement);
    if (el) changed.push(el);
    r.addedNodes.forEach(n => changed.push(n.nodeType === 1 ? n : n.parentElement));
  }));
  window.__jaynesWatch.observe(document.documentElement, {subtree: true, childList: true, attributes: true,
                                                          characterData: true});
})()"""

_DIFFERENCE = """(async (a, b) => {
  const load = src => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src; });
  const [x, y] = await Promise.all([load(a), load(b)]);
  const w = Math.min(x.width, y.width), h = Math.min(x.height, y.height);
  const pixels = img => { const c = document.createElement("canvas"); c.width = w; c.height = h;
    const g = c.getContext("2d"); g.drawImage(img, 0, 0); return g.getImageData(0, 0, w, h).data; };
  const p = pixels(x), q = pixels(y);
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const k = (j * w + i) * 4;
    if (p[k] !== q[k] || p[k + 1] !== q[k + 1] || p[k + 2] !== q[k + 2]) {
      if (i < x0) x0 = i; if (i > x1) x1 = i; if (j < y0) y0 = j; if (j > y1) y1 = j; }
  }
  return x1 < 0 ? [] : [x0, y0, x1 - x0 + 1, y1 - y0 + 1];
})"""

_REGION = """((around) => {
  const vw = innerWidth, vh = innerHeight, pad = 24;
  // a page with no viewport tag is shown zoomed out on a phone: the picture's pixels are its pixels times this
  const k = (window.visualViewport && visualViewport.scale) || 1, pw = Math.round(vw * k), ph = Math.round(vh * k);
  const shown = r => r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
  const visible = el => { const s = getComputedStyle(el); return s.visibility !== "hidden" && s.display !== "none"; };
  const SOLID = "img, svg, canvas, video, input, button, select, textarea";
  // where an element has ink: its text, as laid out, and its pictures and controls; not the empty width of a block
  const inked = el => {
    const out = [];
    if (!el || el.nodeType !== 1) return out;
    if (el.matches(SOLID)) { if (visible(el)) out.push(el.getBoundingClientRect()); return out; }
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (!n.textContent.trim() || !n.parentElement || !visible(n.parentElement)) continue;
      const range = document.createRange(); range.selectNodeContents(n); out.push(range.getBoundingClientRect());
    }
    el.querySelectorAll(SOLID).forEach(x => { if (visible(x)) out.push(x.getBoundingClientRect()); });
    return out;
  };
  const changed = (window.__jaynesChanged || []).filter(el => el && el !== document.body && el !== document.documentElement);
  let boxes = around ? [new DOMRect(around[0] / k, around[1] / k, around[2] / k, around[3] / k)]
                     : changed.flatMap(inked).filter(shown);
  if (!boxes.length) boxes = inked(document.body).filter(shown);
  if (!boxes.length) return [0, 0, pw, ph];
  // never cut through a line or a control: take in whole any the box reaches into
  const all = inked(document.body).filter(shown);
  for (let i = 0; i < 3; i++) {
    const [l, t, r, b] = [Math.min(...boxes.map(x => x.left)) - pad / k, Math.min(...boxes.map(x => x.top)) - pad / k,
                          Math.max(...boxes.map(x => x.right)) + pad / k, Math.max(...boxes.map(x => x.bottom)) + pad / k];
    const more = all.filter(x => !boxes.includes(x) && x.left < r && x.right > l && x.top < b && x.bottom > t);
    if (!more.length) break;
    boxes = boxes.concat(more);
  }
  let x0 = Math.min(...boxes.map(r => r.left)) * k - pad, y0 = Math.min(...boxes.map(r => r.top)) * k - pad;
  let x1 = Math.max(...boxes.map(r => r.right)) * k + pad, y1 = Math.max(...boxes.map(r => r.bottom)) * k + pad;
  const grow = (a, b, least, most) => { const more = least - (b - a); if (more > 0) { a -= more / 2; b += more / 2; }
    if (a < 0) { b -= a; a = 0; } if (b > most) { a -= b - most; b = most; } return [Math.max(0, a), Math.min(most, b)]; };
  [x0, x1] = grow(x0, x1, Math.min(480, pw), pw); [y0, y1] = grow(y0, y1, Math.min(270, ph), ph);
  return [Math.round(x0), Math.round(y0), Math.round(x1 - x0), Math.round(y1 - y0)];
})"""

_CONTROLS = """(() => {
  const shown = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; };
  return [...document.querySelectorAll('button, a[href], [role=button], [role=tab], [role=menuitem], [role=link], input, textarea, select')]
    .filter(shown).slice(0, 300).map(el => ({tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
      name: (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '').trim().slice(0, 80),
      filled: (() => { const s = getComputedStyle(el); return el.tagName === 'BUTTON' && s.backgroundColor !== 'rgba(0, 0, 0, 0)'
        && s.backgroundColor !== 'transparent'; })()}));
})()"""

_FIND = """(kind, text, exact) => {
  const shown = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const same = (t) => { t = (t || '').trim().replace(/\\s+/g, ' '); return exact ? t === text : t.includes(text); };
  let pool;
  if (kind === 'pressButton') {
    pool = [...document.querySelectorAll('button, [role=button], input[type=button], input[type=submit]')]
      .filter(el => same(el.getAttribute('aria-label') || el.innerText || el.value || el.title));
  } else if (kind === 'openRow') {
    pool = [...document.querySelectorAll('tr, li, [role=row], [role=listitem], [role=option], [data-row]')]
      .filter(el => same(el.innerText));
  } else {
    pool = [...document.querySelectorAll('body *')].filter(el => same(el.innerText) &&
      ![...el.children].some(c => same(c.innerText)));
    pool = pool.map(el => el.closest('a, button, [role=button], [role=tab], [role=link], [onclick], summary, label') || el);
  }
  const el = pool.filter(shown)[0];
  if (!el) return null;
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
}"""

_FIELD = """(label) => {
  const all = [...document.querySelectorAll('input, textarea, [contenteditable=true]')];
  const named = el => [el.getAttribute('aria-label'), el.placeholder, el.name,
    el.id && document.querySelector('label[for="' + el.id + '"]')?.innerText,
    el.closest('label')?.innerText].filter(Boolean).map(t => t.trim());
  const el = all.find(el => named(el).some(t => t.includes(label)));
  if (!el) return false;
  el.focus(); if ('value' in el) el.value = '';
  return true;
}"""

_SCROLL = """(region, to) => {
  const el = region === 'page' ? document.scrollingElement : document.querySelector(region);
  if (!el) return false;
  el.scrollTop = to === 'top' ? 0 : to === 'bottom' ? el.scrollHeight : Number(to);
  return true;
}"""
