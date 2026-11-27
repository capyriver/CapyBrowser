#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.2.0,<2",
#     "websockets>=13.0",
# ]
# ///
"""CapyBrowser (browser_mcp.py) - a single-file MCP server that lets an LLM drive the system browser.

Talks to Chrome / Edge / Brave / Chromium directly over the Chrome DevTools Protocol
(WebSocket). No Playwright, Selenium or Puppeteer.
https://github.com/capyriver/CapyBrowser

    uv run browser_mcp.py            # headless (default)
    uv run browser_mcp.py --headed   # visible window, for debugging
    uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser

Options: --browser PATH (or env BROWSER_MCP_PATH), --max-chars N (default 3500),
--lang ru-RU (or env BROWSER_MCP_LANG; default: system locale),
--allow-local-files (or env BROWSER_MCP_ALLOW_FILE=1) to permit file:// URLs.
Tools: web_search, open_url, click, type_text, scroll, go_back.
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

# Configure the interpreter streams before FastMCP/AnyIO captures them.
if os.name == "nt":
    for _stream in (sys.stdin, sys.stdout):
        with contextlib.suppress(Exception):
            _stream.reconfigure(encoding="utf-8", errors="replace")

__version__ = "1.0.0"

log = logging.getLogger("browser_mcp")

MAX_CHARS = int(os.environ.get("BROWSER_MCP_MAX_CHARS", "3500"))
NAV_TIMEOUT = float(os.environ.get("BROWSER_MCP_NAV_TIMEOUT", "25"))
CDP_TIMEOUT = 30.0
TMP_PREFIX = "browser_mcp_"
IS_WIN = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------- browser discovery


def browser_candidates() -> list[str]:
    """Ordered list of possible Chromium-family executables for this OS."""
    system = platform.system()
    out: list[str] = []
    if system == "Windows":
        roots = []
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "LOCALAPPDATA"):
            v = os.environ.get(var)
            if v and v not in roots:
                roots.append(v)
        rels = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
            r"Chromium\Application\chrome.exe",
            r"Google\Chrome Beta\Application\chrome.exe",
            r"Google\Chrome Dev\Application\chrome.exe",
            r"Google\Chrome SxS\Application\chrome.exe",
            r"Microsoft\Edge Beta\Application\msedge.exe",
            r"Microsoft\Edge Dev\Application\msedge.exe",
            r"Vivaldi\Application\vivaldi.exe",
        ]
        for rel in rels:
            for root in roots:
                out.append(os.path.join(root, rel))
        names = ["chrome", "msedge", "brave", "chromium"]
    elif system == "Darwin":
        apps = [
            ("Google Chrome", "Google Chrome"),
            ("Microsoft Edge", "Microsoft Edge"),
            ("Brave Browser", "Brave Browser"),
            ("Chromium", "Chromium"),
            ("Google Chrome Beta", "Google Chrome Beta"),
            ("Google Chrome Dev", "Google Chrome Dev"),
            ("Google Chrome Canary", "Google Chrome Canary"),
            ("Google Chrome for Testing", "Google Chrome for Testing"),
            ("Microsoft Edge Beta", "Microsoft Edge Beta"),
            ("Vivaldi", "Vivaldi"),
        ]
        for app, binary in apps:
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                out.append(f"{base}/{app}.app/Contents/MacOS/{binary}")
        names = ["google-chrome", "chromium", "chrome", "microsoft-edge", "brave-browser"]
    else:
        names = [
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
            "microsoft-edge", "microsoft-edge-stable", "brave-browser", "brave",
            "google-chrome-beta", "google-chrome-unstable", "chrome", "vivaldi",
        ]
        out += [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome",
            "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/lib/chromium/chromium",
            "/opt/microsoft/msedge/msedge", "/usr/bin/microsoft-edge",
            "/opt/brave.com/brave/brave", "/usr/bin/brave-browser",
            "/snap/bin/chromium", "/snap/bin/brave",
            "/var/lib/flatpak/exports/bin/com.google.Chrome",
            "/var/lib/flatpak/exports/bin/org.chromium.Chromium",
            "/var/lib/flatpak/exports/bin/com.brave.Browser",
            "/var/lib/flatpak/exports/bin/com.microsoft.Edge",
        ]
    found = [p for p in (shutil.which(n) for n in names) if p]
    result: list[str] = []
    for p in found + out:
        if p and p not in result:
            result.append(p)
    return result


def find_browser(explicit: str | None = None) -> str | None:
    for cand in (explicit, os.environ.get("BROWSER_MCP_PATH"), os.environ.get("CHROME_PATH")):
        if cand:
            p = shutil.which(cand) or cand
            if os.path.isfile(p):
                return p
    for p in browser_candidates():
        if os.path.isfile(p) and (IS_WIN or os.access(p, os.X_OK)):
            return p
    return None


def normalize_lang(value: Any) -> str | None:
    """'ru_RU.UTF-8' -> 'ru-RU', 'de' -> 'de'; None for C/POSIX/garbage."""
    m = re.match(r"^\s*([A-Za-z]{2,3})(?:[_-]([A-Za-z]{2}|\d{3}))?(?=$|[.@:\s])", str(value or ""))
    if not m:
        return None
    lang = m.group(1).lower()
    return f"{lang}-{m.group(2).upper()}" if m.group(2) else lang


def system_lang() -> str:
    """UI language for the browser: BROWSER_MCP_LANG, else the OS locale, else en-US."""
    def first(cands: list[Any]) -> str | None:
        for c in cands:
            n = normalize_lang(c)
            if n:
                return n
        return None

    found = first([os.environ.get("BROWSER_MCP_LANG")])
    if not found and IS_WIN:
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):  # type: ignore[attr-defined]
                found = first([buf.value])
        except Exception:
            pass
    if not found:
        lng = (os.environ.get("LANGUAGE") or "").split(":")[0]
        found = first([os.environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG")] + [lng])
    if not found and sys.platform == "darwin":
        try:
            found = first([subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True,
                                          text=True, timeout=2).stdout.strip()])
        except Exception:
            pass
    if not found:
        with contextlib.suppress(Exception):
            import locale

            found = first([locale.getlocale()[0]])
    return found or "en-US"


def accept_language(lang: str) -> str:
    """Plain language list for Emulation.setUserAgentOverride (Chrome adds the q-values itself)."""
    base = lang.split("-")[0]
    parts = [lang] + ([base] if base != lang else [])
    if base != "en":
        parts.append("en")
    return ",".join(parts)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Local HTTP to the DevTools endpoint must never go through an HTTP(S) proxy.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_text(url: str, method: str = "GET", timeout: float = 5.0) -> str:
    req = urllib.request.Request(url, method=method)
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _http_json(url: str, method: str = "GET", timeout: float = 5.0) -> Any:
    return json.loads(_http_text(url, method, timeout) or "null")


# --------------------------------------------------------------------------- process cleanup

_LIVE: set["Browser"] = set()
_LIVE_LOCK = threading.RLock()  # re-entrant: cleanup may run from a signal handler in the same thread


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WIN:
        # Avoid parsing localized tasklist columns when the WinAPI can answer
        # directly. PROCESS_QUERY_LIMITED_INFORMATION works for ordinary PIDs.
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            open_process.restype = wintypes.HANDLE
            get_exit_code = kernel32.GetExitCodeProcess
            get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            get_exit_code.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL

            handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                try:
                    code = wintypes.DWORD()
                    if get_exit_code(handle, ctypes.byref(code)):
                        return code.value == 259  # STILL_ACTIVE
                finally:
                    close_handle(handle)
        except Exception:
            pass
        # Fallback for restricted/older Windows configurations. CSV keeps the
        # PID in column two regardless of localized memory units/column layout.
        try:
            import csv
            import io

            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                    capture_output=True, text=True, timeout=5)
            for row in csv.reader(io.StringIO(result.stdout)):
                if len(row) > 1 and row[1].strip() == str(pid):
                    return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _rmtree(path: str | None, attempts: int = 60) -> None:
    if not path:
        return
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.1)
    shutil.rmtree(path, ignore_errors=True)


def cleanup_stale_dirs() -> None:
    """Remove temp profiles left behind by previous runs that were killed hard."""
    base = tempfile.gettempdir()
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not name.startswith(TMP_PREFIX):
            continue
        path = os.path.join(base, name)
        try:
            with open(os.path.join(path, "owner.pid"), encoding="ascii") as f:
                owner = int(f.read().strip() or "0")
        except (OSError, ValueError):
            try:  # no owner file: only remove when clearly old
                if time.time() - os.path.getmtime(path) < 3600:
                    continue
            except OSError:
                continue
            owner = -1
        if owner != os.getpid() and not _pid_alive(owner):
            _rmtree(path, attempts=3)


def cleanup_all() -> None:
    # Never block here (may run inside a signal handler): copy the set without waiting for the lock.
    got = _LIVE_LOCK.acquire(timeout=1.0)
    try:
        items = list(_LIVE)
    finally:
        if got:
            _LIVE_LOCK.release()
    for b in items:
        with contextlib.suppress(Exception):
            b.kill_sync()


def _reap_zombies() -> None:
    """Reap zombie children adopted by us (we are a child subreaper on Linux).

    Only zombies that are not a live Browser's own main process are reaped, so the exit status of
    processes owned by subprocess.Popen objects is never stolen."""
    if not IS_LINUX:
        return
    me = os.getpid()
    with _LIVE_LOCK:
        owned = {b.proc.pid for b in _LIVE if b.proc is not None}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for name in entries:
        if not name.isdigit() or int(name) in owned:
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="ascii", errors="replace") as f:
                stat = f.read()
            fields = stat[stat.rindex(")") + 2:].split()
            if fields[0] == "Z" and int(fields[1]) == me:
                os.waitpid(int(name), os.WNOHANG)
        except (OSError, ValueError, IndexError, ChildProcessError):
            continue


def _linux_prctl(option: int, arg: int) -> None:
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(option, arg, 0, 0, 0)
    except Exception:
        pass


def _child_preexec() -> None:  # runs in the forked child before exec (Linux only)
    _linux_prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG: die with the MCP server


# --------------------------------------------------------------------------- CDP client


class CDPError(Exception):
    pass


class CDPTimeout(CDPError):
    pass


class PageHung(CDPTimeout):
    pass


class _Waiter:
    def __init__(self, cdp: "CDP", method: str, pred: Callable[[dict], bool] | None):
        self.cdp, self.method, self.pred = cdp, method, pred
        self.fut: asyncio.Future = asyncio.get_running_loop().create_future()

    async def wait(self, timeout: float) -> dict | None:
        try:
            return await asyncio.wait_for(asyncio.shield(self.fut), timeout)
        except (asyncio.TimeoutError, ConnectionError):
            return None

    def done(self) -> bool:
        return self.fut.done()

    def cancel(self) -> None:
        with contextlib.suppress(ValueError):
            self.cdp._waiters.remove(self)
        if not self.fut.done():
            self.fut.cancel()


class CDP:
    """Minimal CDP client over one WebSocket (one page target)."""

    def __init__(self, ws: Any):
        self.ws = ws
        self.closed = False
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._waiters: list[_Waiter] = []
        self.on_event: Callable[[str, dict], None] | None = None
        self._reader = asyncio.create_task(self._read_loop())

    @classmethod
    async def connect(cls, url: str) -> "CDP":
        from websockets.asyncio.client import connect

        kw: dict[str, Any] = dict(max_size=None, ping_interval=None, open_timeout=15, close_timeout=2)
        try:
            ws = await connect(url, proxy=None, **kw)
        except TypeError:  # websockets < 15 has no proxy support/argument
            ws = await connect(url, **kw)
        return cls(ws)

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            err = msg["error"]
                            fut.set_exception(CDPError(f"{err.get('message')} {err.get('data', '')}".strip()))
                        else:
                            fut.set_result(msg.get("result", {}))
                    continue
                method, params = msg.get("method", ""), msg.get("params") or {}
                for w in list(self._waiters):
                    if w.method == method and not w.fut.done():
                        try:
                            ok = w.pred is None or w.pred(params)
                        except Exception:
                            ok = False
                        if ok:
                            w.fut.set_result(params)
                            with contextlib.suppress(ValueError):
                                self._waiters.remove(w)
                if self.on_event:
                    with contextlib.suppress(Exception):
                        self.on_event(method, params)
        except Exception:
            pass
        finally:
            self.closed = True
            err = ConnectionError("CDP connection closed")
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(err)
            self._pending.clear()
            for w in list(self._waiters):
                if not w.fut.done():
                    w.fut.set_exception(err)
                    w.fut.exception()  # mark retrieved
            self._waiters.clear()

    def waiter(self, method: str, pred: Callable[[dict], bool] | None = None) -> _Waiter:
        w = _Waiter(self, method, pred)
        self._waiters.append(w)
        return w

    async def send(self, method: str, params: dict | None = None, timeout: float = CDP_TIMEOUT) -> dict:
        if self.closed:
            raise ConnectionError("CDP connection closed")
        self._id += 1
        mid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        except Exception as e:
            self._pending.pop(mid, None)
            raise ConnectionError(f"CDP send failed: {e}") from e
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise CDPTimeout(f"CDP {method} timed out after {timeout:.0f}s") from None

    async def close(self) -> None:
        self.closed = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.ws.close(), 2)
        self._reader.cancel()
        with contextlib.suppress(BaseException):
            await self._reader


# --------------------------------------------------------------------------- in-page JavaScript

INIT_JS = r"""
(() => {
  if (window.top !== window) return;
  try {
    window.open = function (u) {
      try { if (u) location.href = new URL(String(u), location.href).href; } catch (e) {}
      return window;
    };
  } catch (e) {}
})();
"""

# Shared helpers: element registry, visibility, labels.
_JS_LIB = r"""
const MCP = (() => {
  const W = window;
  const vis = new Map();
  function visible(el) {
    if (!el || el.nodeType !== 1) return false;
    let v = vis.get(el);
    if (v !== undefined) return v;
    try {
      if (el.checkVisibility) v = el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
      else { const s = getComputedStyle(el); v = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'; }
    } catch (e) { v = false; }
    vis.set(el, v);
    return v;
  }
  function clean(s, n) {
    s = String(s == null ? '' : s).replace(/[\s ​]+/g, ' ').trim();
    if (n && s.length > n) s = s.slice(0, n - 1) + '…';
    return s;
  }
  function q(s) { return '"' + String(s).replace(/"/g, "'") + '"'; }
  function reset() {
    const old = W.__mcpMarked || [];
    for (const el of old) { try { el.removeAttribute('data-mcp-id'); } catch (e) {} }
    W.__mcpMarked = []; W.__mcpMap = new Map(); W.__mcpNext = 1;
  }
  function mark(el) {
    const id = W.__mcpNext++;
    try { el.setAttribute('data-mcp-id', String(id)); } catch (e) {}
    W.__mcpMarked.push(el); W.__mcpMap.set(id, el);
    return id;
  }
  function textOf(el, n) {
    let t = '';
    try { t = el.innerText; } catch (e) {}
    if (!t) t = el.textContent || '';
    return clean(t, n);
  }
  function labelOf(el) {
    const a = el.getAttribute('aria-label'); if (a && clean(a)) return clean(a, 80);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i => { const e = document.getElementById(i); return e ? textOf(e, 80) : ''; }).join(' '); if (clean(t)) return clean(t, 80); }
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      try { if (el.labels && el.labels.length) { const t = textOf(el.labels[0], 80); if (t) return t; } } catch (e) {}
      for (const k of ['placeholder', 'title', 'name', 'id']) { const v = el.getAttribute(k); if (v && clean(v)) return clean(v, 80); }
      if (tag === 'INPUT' && ['submit', 'button', 'reset'].includes((el.type || '').toLowerCase())) return clean(el.value || el.type, 80);
      if (tag === 'INPUT' && (el.type || '').toLowerCase() === 'image') return clean(el.alt || 'submit', 80);
      return '';
    }
    let t = textOf(el, 80);
    if (t) return t;
    for (const k of ['title', 'alt', 'value', 'name']) { const v = el.getAttribute(k); if (v && clean(v)) return clean(v, 80); }
    const img = el.querySelector && el.querySelector('img[alt],svg[aria-label],[title]');
    if (img) { const v = img.getAttribute('alt') || img.getAttribute('aria-label') || img.getAttribute('title'); if (v && clean(v)) return clean(v, 80); }
    if (tag === 'A') { const h = el.getAttribute('href') || ''; return clean(h, 60); }
    return '';
  }
  function kindOf(el) {
    const tag = el.tagName.toLowerCase(), role = (el.getAttribute('role') || '').toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox' || t === 'radio') return t;
      return t === 'text' ? 'input' : 'input:' + t;
    }
    if (tag === 'textarea') return 'textarea';
    if (tag === 'select') return 'select';
    if (role) return role;
    if (el.isContentEditable) return 'editable';
    if (tag === 'summary') return 'summary';
    return tag;
  }
  function describe(el, id) {
    let ctl = null;
    try { if (el.tagName === 'LABEL' && el.control && /^(checkbox|radio)$/i.test(el.control.type)) ctl = el.control; } catch (e) {}
    if (ctl) return '[' + id + '] ' + ctl.type.toLowerCase() + ' ' + q(textOf(el, 80) || labelOf(ctl)) + (ctl.checked ? ' (checked)' : ' (unchecked)');
    const kind = kindOf(el);
    let s = '[' + id + '] ' + kind;
    const name = labelOf(el);
    if (name) s += ' ' + q(name);
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') {
      const t = (el.type || '').toLowerCase();
      if (t === 'checkbox' || t === 'radio') s += el.checked ? ' (checked)' : ' (unchecked)';
      else if (!['submit', 'button', 'reset', 'image'].includes(t) && el.value) s += ' value=' + q(t === 'password' ? '***' : clean(el.value, 40));
    } else if (tag === 'SELECT') {
      const opts = Array.from(el.options || []);
      const cur = opts[el.selectedIndex];
      if (cur) s += ' selected=' + q(clean(cur.text, 30));
      const list = opts.slice(0, 8).map(o => clean(o.text, 20)).filter(Boolean);
      if (list.length) s += ' options: ' + list.join(' | ') + (opts.length > 8 ? ' | …' : '');
    } else if (el.isContentEditable && kind === 'editable') {
      // label already contains the text
    }
    if (el.disabled) s += ' (disabled)';
    return s;
  }
  function deepActive(root) { let a = root.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement; return a; }
  function deepHit(x, y) {
    let h = document.elementFromPoint(x, y);
    let guard = 0;
    while (h && h.shadowRoot && guard++ < 20) { const inner = h.shadowRoot.elementFromPoint(x, y); if (!inner || inner === h) break; h = inner; }
    return h;
  }
  function contains(a, b) {
    // shadow-aware a.contains(b)
    let n = b, guard = 0;
    while (n && guard++ < 10000) { if (n === a) return true; n = n.parentNode || (n.host || null); }
    return false;
  }
  return {visible, clean, reset, mark, labelOf, describe, deepActive, deepHit, contains, textOf};
})();
"""

SNAPSHOT_JS = "(() => {" + _JS_LIB + r"""
  const W = window, D = document, vh = W.innerHeight || 800, vw = W.innerWidth || 1200;
  MCP.reset();
  const SEL = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link],[role=tab],' +
    '[role=menuitem],[role=menuitemcheckbox],[role=menuitemradio],[role=checkbox],[role=radio],[role=switch],[role=option],' +
    '[role=combobox],[role=searchbox],[role=textbox],[role=treeitem],[onclick],[contenteditable=""],[contenteditable=true],' +
    '[contenteditable=plaintext-only],[tabindex]:not([tabindex="-1"])';
  const NEST = 'a[href],button,[role=button],[role=link]';
  const cands = [];
  let scanned = 0;
  function collect(root, depth) {
    if (depth > 8) return;
    let list;
    try { list = root.querySelectorAll(SEL); } catch (e) { return; }
    for (const el of list) { cands.push(el); if (cands.length > 4000) return; }
    let all;
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of all) {
      if (++scanned > 30000) return;
      if (el.shadowRoot) collect(el.shadowRoot, depth + 1);
    }
  }
  if (D.body) collect(D, 0);
  const groups = [[], [], []];   // in view (content), in view (nav/aside), just below the fold
  let below = 0;
  const seen = new Set();
  const NAVSEL = 'nav,aside,[role=navigation],[role=complementary],[role=banner] nav';
  for (let el of cands) {
    if (seen.has(el)) continue; seen.add(el);
    if (!MCP.visible(el)) {
      // custom-styled checkbox/radio: the input is invisible but its <label> is clickable
      let lab = null;
      try { if (el.tagName === 'INPUT' && /^(checkbox|radio)$/i.test(el.type) && el.labels && el.labels.length) lab = el.labels[0]; } catch (e) {}
      if (!lab || seen.has(lab) || !MCP.visible(lab)) continue;
      el = lab; seen.add(el);
    }
    const tag = el.tagName;
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
      const p = el.parentElement && el.parentElement.closest ? el.parentElement.closest(NEST) : null;
      if (p) continue;
    }
    let r;
    try { r = el.getBoundingClientRect(); } catch (e) { continue; }
    if (r.width < 1 || r.height < 1) continue;
    if (r.right < 0 || r.left > vw * 1.5) continue;
    if (r.bottom <= 0) continue;              // scrolled past
    if (r.top > vh * 2.5) { below++; continue; } // far below: available after scrolling
    let g = 2;
    if (r.top < vh) { let nav = null; try { nav = el.closest(NAVSEL); } catch (e) {} g = nav ? 1 : 0; }
    groups[g].push(el);
  }
  const elems = [];
  for (const g of groups) for (const el of g) {
    if (elems.length >= 150) { below++; continue; }
    const id = MCP.mark(el);
    elems.push(MCP.describe(el, id));
  }
  // ---- visible text from the current scroll position downwards
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'SVG', 'CANVAS', 'IFRAME', 'OBJECT']);
  const BLOCK = /^(block|flex|grid|list-item|table|table-row|table-caption|flow-root)/;
  const blockCache = new Map();
  function blockOf(el) {
    let n = el, g = 0;
    while (n && n !== D.body && g++ < 60) {
      let b = blockCache.get(n);
      if (b === undefined) { try { b = BLOCK.test(getComputedStyle(n).display); } catch (e) { b = false; } blockCache.set(n, b); }
      if (b) return n;
      n = n.parentElement;
    }
    return D.body;
  }
  const lines = [];
  let cur = '', curBlock = null, total = 0, SKIPSEL = null;
  // Text of small controls (buttons, inputs, tabs...) is already in the element list. Links and
  // container-like elements (cards/modals with tabindex/onclick) keep their text in the TEXT section.
  const CTRL_TAGS = new Set(['BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'SUMMARY', 'OPTION', 'LABEL']);
  const CTRL_ROLES = new Set(['button', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option', 'checkbox',
                              'radio', 'switch', 'combobox', 'searchbox', 'textbox', 'treeitem']);
  const ctrlCache = new Map();
  function isControl(m) {
    let v = ctrlCache.get(m);
    if (v !== undefined) return v;
    v = false;
    if (m.tagName !== 'A') {
      const role = (m.getAttribute('role') || '').toLowerCase();
      if (CTRL_TAGS.has(m.tagName) || CTRL_ROLES.has(role)) {
        let t = '';
        try { t = m.innerText || ''; } catch (e) {}
        v = t.length <= 200 && !(m.querySelector && m.querySelector('p,h1,h2,h3,h4,h5,h6,li,table,article,section'));
      }
    }
    ctrlCache.set(m, v);
    return v;
  }
  const LIMIT = 6000;
  function flush() { const t = cur.trim(); if (t && t !== lines[lines.length - 1]) { lines.push(t); total += t.length + 1; } cur = ''; }
  function walkText(root, depth) {
    if (depth > 8 || total > LIMIT) return;
    let tw;
    try {
      tw = D.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
        acceptNode(n) {
          if (n.nodeType === 1) {
            if (SKIP.has(n.tagName.toUpperCase())) return NodeFilter.FILTER_REJECT;
            if (SKIPSEL && n.matches && n.matches(SKIPSEL) && (n.innerText || '').length < 3000) return NodeFilter.FILTER_REJECT;
            return n.shadowRoot ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
          }
          return /\S/.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        }
      });
    } catch (e) { return; }
    let n, steps = 0;
    while ((n = tw.nextNode())) {
      if (++steps > 60000 || total > LIMIT) break;
      if (n.nodeType === 1) { if (n.shadowRoot) walkText(n.shadowRoot, depth + 1); continue; }
      const p = n.parentElement;
      if (!p || !MCP.visible(p)) continue;
      { const m = p.closest && p.closest('[data-mcp-id]'); if (m && isControl(m)) continue; } // short controls are listed as elements
      let r;
      try { r = p.getBoundingClientRect(); } catch (e) { continue; }
      if (r.bottom <= 0 && r.height > 0) continue;
      const b = blockOf(p);
      if (b !== curBlock) { flush(); curBlock = b; }
      cur += (cur ? ' ' : '') + MCP.clean(n.nodeValue);
      if (cur.length > 2000) flush();
    }
  }
  let root = D.body;
  try {
    const mains = Array.from(D.querySelectorAll('main,[role=main]')).filter(m => MCP.visible(m) && (m.innerText || '').trim().length > 200);
    if (mains.length === 1) root = mains[0];
  } catch (e) {}
  SKIPSEL = 'nav,[role=navigation]';
  if (root) walkText(root, 0);
  if (root !== D.body && total < 200 && D.body) { SKIPSEL = null; walkText(D.body, 0); }
  flush();
  const se = D.scrollingElement || D.documentElement;
  const sh = Math.max(se ? se.scrollHeight : 0, D.body ? D.body.scrollHeight : 0);
  let status = 0;
  try { const nav = performance.getEntriesByType('navigation')[0]; status = nav && nav.responseStatus || 0; } catch (e) {}
  return {
    url: location.href, title: D.title || '', status,
    scrollY: Math.round(W.scrollY || (se ? se.scrollTop : 0)), viewport: vh, height: Math.round(sh),
    text: lines.join('\n'), elements: elems, more: below
  };
})()
"""

# Locate a registered element and prepare a click. Returns click coordinates or does a JS click.
CLICK_JS = "((id) => {" + _JS_LIB + r"""
  const map = window.__mcpMap;
  const el = map && map.get(id);
  if (!el || !el.isConnected) return {ok: false, reason: 'notfound'};
  if (el.disabled) return {ok: false, reason: 'disabled'};
  try { if (el.tagName === 'A' && el.target && el.target !== '_self') el.target = '_self'; } catch (e) {}
  try { const f = el.form || (el.closest && el.closest('form')); if (f && f.target && f.target !== '_self') f.target = '_self'; } catch (e) {}
  try { el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); } catch (e) { try { el.scrollIntoView(); } catch (e2) {} }
  const desc = MCP.labelOf(el);
  const rects = Array.from(el.getClientRects()).filter(r => r.width >= 1 && r.height >= 1);
  const vw = window.innerWidth, vh = window.innerHeight;
  const pts = [];
  for (const r of rects.slice(0, 3)) {
    pts.push([r.left + r.width / 2, r.top + r.height / 2]);
    pts.push([r.left + Math.min(5, r.width / 2), r.top + r.height / 2]);
    pts.push([r.left + r.width / 2, r.top + Math.min(5, r.height / 2)]);
  }
  for (const [x, y] of pts) {
    if (x < 0 || y < 0 || x >= vw || y >= vh) continue;
    const hit = MCP.deepHit(x, y);
    if (!hit) continue;
    let ok = hit === el || MCP.contains(el, hit);
    if (!ok && hit.tagName === 'LABEL') { try { ok = hit.control === el; } catch (e) {} }
    if (ok) return {ok: true, mode: 'mouse', x, y, desc};
  }
  // Obscured, zero-size or off-screen: fall back to a DOM click.
  try { el.focus && el.focus({preventScroll: true}); } catch (e) {}
  try { el.click(); } catch (e) { return {ok: false, reason: 'error: ' + e}; }
  return {ok: true, mode: 'js', desc};
})
"""

FOCUS_JS = "((id, clear) => {" + _JS_LIB + r"""
  const map = window.__mcpMap;
  let el = map && map.get(id);
  if (!el || !el.isConnected) return {ok: false, reason: 'notfound'};
  const TEXTY = new Set(['', 'text', 'search', 'email', 'url', 'tel', 'password', 'number', 'date', 'datetime-local', 'month', 'time', 'week']);
  const editable = e => e && ((e.tagName === 'INPUT' && TEXTY.has((e.getAttribute('type') || '').toLowerCase())) || e.tagName === 'TEXTAREA' || e.isContentEditable);
  if (el.tagName === 'SELECT') return {ok: true, kind: 'select'};
  if (!editable(el)) {
    let inner = null;
    try { if (el.tagName === 'LABEL' && el.control) inner = el.control; } catch (e) {}
    if (!inner && el.querySelector) inner = el.querySelector('input:not([type=hidden]),textarea,[contenteditable=""],[contenteditable=true],[contenteditable=plaintext-only]');
    if (!inner && el.shadowRoot) inner = el.shadowRoot.querySelector('input:not([type=hidden]),textarea,[contenteditable]');
    if (inner && inner.tagName === 'SELECT') { window.__mcpTarget = inner; return {ok: true, kind: 'select'}; }
    if (!editable(inner)) return {ok: false, reason: 'noteditable', kind: el.tagName.toLowerCase() + (el.type ? ':' + el.type : '')};
    el = inner;
  }
  if (el.disabled || el.readOnly) return {ok: false, reason: 'disabled'};
  window.__mcpTarget = el;
  try { el.scrollIntoView({block: 'center', behavior: 'instant'}); } catch (e) {}
  try { el.focus({preventScroll: true}); } catch (e) { try { el.focus(); } catch (e2) {} }
  const isCE = !(el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
  if (clear) {
    if (!isCE) { try { el.select(); } catch (e) {} try { el.setSelectionRange && el.setSelectionRange(0, el.value.length); } catch (e) {} }
    else { try { const s = getSelection(); s.removeAllRanges(); const r = document.createRange(); r.selectNodeContents(el); s.addRange(r); } catch (e) {} }
  } else {
    if (!isCE) { try { const n = el.value.length; el.setSelectionRange && el.setSelectionRange(n, n); } catch (e) {} }
    else { try { const s = getSelection(); s.removeAllRanges(); const r = document.createRange(); r.selectNodeContents(el); r.collapse(false); s.addRange(r); } catch (e) {} }
  }
  const focused = MCP.deepActive(document) === el;
  const before = isCE ? (el.innerText || '') : (el.value || '');
  // Watch for form submission (used to decide whether Enter worked).
  window.__mcpSubmitted = false;
  try { document.addEventListener('submit', () => { window.__mcpSubmitted = true; }, {capture: true, once: true}); } catch (e) {}
  return {ok: true, kind: isCE ? 'editable' : 'input', focused, before, type: (el.type || '').toLowerCase()};
})
"""

VERIFY_JS = r"""
((text, clear) => {
  const el = window.__mcpTarget;
  if (!el || !el.isConnected) return {ok: false, value: null};
  const isCE = !(el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
  const norm = s => String(s || '').replace(/[\s ]+/g, ' ').trim();
  const val = () => isCE ? (el.innerText || el.textContent || '') : (el.value || '');
  if (norm(val()).includes(norm(text))) return {ok: true, value: val(), mode: 'keyboard'};
  // Input masks and controlled widgets may transform the text during the real
  // keyboard insertion. If the control is non-empty and the typed characters
  // are represented in order, keep the browser's accepted value untouched.
  // This prevents the fallback setter from replacing e.g. (999) 123-45-67.
  const accepted = val();
  const compact = s => String(s || '').replace(/[^\p{L}\p{N}]/gu, '').toLocaleLowerCase();
  const wanted = compact(text), actual = compact(accepted);
  if (wanted && actual && actual.includes(wanted))
    return {ok: true, value: accepted, mode: 'keyboard-transformed'};
  if (wanted && actual) {
    let i = 0;
    for (const ch of actual) if (ch === wanted[i]) i++;
    if (i === wanted.length) return {ok: true, value: accepted, mode: 'keyboard-transformed'};
  }
  // Fallback: set value through the native setter so React/Vue/etc. notice it.
  try {
    if (!isCE) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, clear ? text : (el.value || '') + text);
    } else {
      if (clear) el.textContent = text; else el.textContent = (el.textContent || '') + text;
    }
    el.dispatchEvent(new InputEvent('input', {bubbles: true, composed: true, inputType: 'insertText', data: text}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  } catch (e) {}
  return {ok: norm(val()).includes(norm(text)), value: val(), mode: 'setter'};
})
"""

SELECT_JS = r"""
((id, text) => {
  let el = window.__mcpTarget && window.__mcpTarget.tagName === 'SELECT' ? window.__mcpTarget : (window.__mcpMap && window.__mcpMap.get(id));
  if (!el || el.tagName !== 'SELECT') return {ok: false};
  const want = String(text).trim().toLowerCase();
  const opts = Array.from(el.options);
  let o = opts.find(o => o.text.trim().toLowerCase() === want) || opts.find(o => o.value.toLowerCase() === want) ||
          opts.find(o => o.text.trim().toLowerCase().includes(want));
  if (!o) return {ok: false, options: opts.slice(0, 30).map(o => o.text.trim())};
  el.value = o.value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, selected: o.text.trim()};
})
"""

SUBMIT_FALLBACK_JS = r"""
(() => {
  if (window.__mcpSubmitted) return 'already';
  const el = window.__mcpTarget;
  const f = el && (el.form || (el.closest && el.closest('form')));
  if (!f) return 'noform';
  try { if (f.target && f.target !== '_self') f.target = '_self'; } catch (e) {}
  try { if (f.requestSubmit) f.requestSubmit(); else f.submit(); return 'submitted'; } catch (e) {
    try { f.submit(); return 'submitted'; } catch (e2) { return 'error'; }
  }
})()
"""

SCROLL_JS = r"""
((dir) => {
  const D = document, W = window;
  const se = D.scrollingElement || D.documentElement;
  const can = e => e && e.scrollHeight > e.clientHeight + 4;
  let t = se;
  let docScrollable = can(se);
  try { const s = getComputedStyle(D.body); if (s.overflowY === 'hidden' || getComputedStyle(D.documentElement).overflowY === 'hidden') docScrollable = docScrollable && false; } catch (e) {}
  if (!docScrollable) {
    let best = null, area = 0, n = 0;
    for (const e of D.querySelectorAll('*')) {
      if (++n > 20000) break;
      if (!can(e)) continue;
      const s = getComputedStyle(e);
      if (!/(auto|scroll|overlay)/.test(s.overflowY)) continue;
      const r = e.getBoundingClientRect();
      const a = Math.max(0, Math.min(r.right, W.innerWidth) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, W.innerHeight) - Math.max(r.top, 0));
      if (a > area) { area = a; best = e; }
    }
    if (best) t = best;
  }
  const isDoc = t === se;
  const before = isDoc ? (W.scrollY || se.scrollTop) : t.scrollTop;
  const page = Math.max(100, (isDoc ? W.innerHeight : t.clientHeight) * 0.85);
  const max = Math.max(0, t.scrollHeight - (isDoc ? W.innerHeight : t.clientHeight));
  let target = before;
  if (dir === 'down') target = before + page; else if (dir === 'up') target = before - page;
  else if (dir === 'top') target = 0; else if (dir === 'bottom') target = max;
  target = Math.max(0, Math.min(max, target));
  if (isDoc) W.scrollTo({top: target, behavior: 'instant'}); else t.scrollTo({top: target, behavior: 'instant'});
  const after = isDoc ? (W.scrollY || se.scrollTop) : t.scrollTop;
  return {before: Math.round(before), after: Math.round(after), max: Math.round(max), container: isDoc ? 'page' : (t.tagName || '').toLowerCase()};
})
"""

SEARCH_JS = "(() => {" + _JS_LIB + r"""
  MCP.reset();
  const out = [];
  const add = (a, snip) => {
    if (!a || !MCP.visible(a)) return;
    const title = MCP.clean(a.innerText || a.textContent, 120);
    if (!title) return;
    out.push({id: MCP.mark(a), title, href: a.href, snippet: MCP.clean(snip, 240)});
  };
  const host = location.hostname;
  if (/duckduckgo\.com$/.test(host)) {
    const items = document.querySelectorAll('.result, .web-result');
    if (items.length) {
      for (const it of items) {
        if (it.classList.contains('result--ad')) continue;
        const a = it.querySelector('a.result__a');
        const s = it.querySelector('.result__snippet');
        add(a, s ? s.innerText : '');
        if (out.length >= 10) break;
      }
    } else {
      for (const a of document.querySelectorAll('a.result-link')) {
        const row = a.closest('tr');
        let snip = '';
        let n = row, k = 0;
        while (n && k++ < 3) { n = n.nextElementSibling; const td = n && n.querySelector('.result-snippet'); if (td) { snip = td.innerText; break; } }
        add(a, snip);
        if (out.length >= 10) break;
      }
    }
  } else if (/bing\.com$/.test(host)) {
    for (const li of document.querySelectorAll('li.b_algo')) {
      const a = li.querySelector('h2 a');
      const p = li.querySelector('.b_caption p, p');
      add(a, p ? p.innerText : '');
      if (out.length >= 10) break;
    }
  }
  const body = document.body ? (document.body.innerText || '') : '';
  const blocked = /anomaly|captcha|unusual traffic|are you a robot|not a robot/i.test(body.slice(0, 3000)) || !!document.querySelector('#challenge-form, .anomaly-modal');
  const noresults = /no results|no more results|не найдено/i.test(body.slice(0, 3000));
  return {results: out, blocked, noresults, title: document.title};
})()
"""


# --------------------------------------------------------------------------- output compaction


def _cut(s: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    if n == 1:
        return "…"
    cut = s[: n - 1]
    sp = cut.rfind(" ")
    if sp > n * 0.7:
        cut = cut[:sp]
    return cut + "…"


def compose(data: dict, max_chars: int = MAX_CHARS, note: str = "") -> str:
    """Build the compact page view: header, visible text, clickable elements. Never exceeds max_chars."""
    max_chars = max(1, int(max_chars))
    if not isinstance(data, dict):
        data = {}

    def num(k: str) -> int:
        try:
            return int(data.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    url = _cut(str(data.get("url") or ""), 300)
    title = _cut(re.sub(r"\s+", " ", str(data.get("title") or "")).strip(), 150)
    head = []
    if note:
        head.append(_cut(note.strip(), 400))
    head.append(f"URL: {url}")
    if title:
        head.append(f"Title: {title}")
    status = num("status")
    if status >= 400:
        head.append(f"HTTP status: {status} (error page)")
    sy, vp, h = num("scrollY"), max(1, num("viewport")), num("height")
    footer = ""
    if h > vp * 1.05:
        pct = min(100, round(100 * (sy + vp) / max(h, 1)))
        at_end = sy + vp >= h - 2
        head.append(f"Scroll: {pct}% of page seen" + (" (end of page)" if at_end else ""))
        if not at_end:
            footer = f"\n[Only {pct}% of the page shown. Call scroll('down') to read further.]"
    header = "\n".join(head)

    text = str(data.get("text") or "").strip()
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    raw_elems = data.get("elements") or []
    elems = [_cut(str(e).replace("\n", " "), 160) for e in raw_elems if isinstance(e, (str, int, float))]
    more = num("more")

    t_label, e_label = "\n--- TEXT ---\n", "\n--- ELEMENTS (use the [id] with click/type_text) ---\n"
    fixed = len(header) + len(t_label) + len(e_label) + len(footer)
    budget = max_chars - fixed - 50  # 50: room for the "(+N more elements ...)" line
    if budget < 100:
        return _cut(header, max_chars)
    e_total = sum(len(e) + 1 for e in elems)
    # Elements get up to ~45% unless the text is short; text gets the rest.
    e_budget = min(e_total, max(int(budget * 0.45), budget - len(text)))
    chosen: list[str] = []
    used = 0
    for e in elems:
        if used + len(e) + 1 > e_budget:
            break
        chosen.append(e)
        used += len(e) + 1
    hidden = len(elems) - len(chosen) + more
    e_part = "\n".join(chosen) if chosen else "(no clickable elements in view)"
    if hidden > 0:
        e_part += f"\n(+{hidden} more elements; scroll to see them)"
    t_budget = max_chars - fixed - len(e_part)
    body_text = _cut(text, t_budget) if text else _cut("(no visible text)", t_budget)
    out = header + t_label + body_text + e_label + e_part + footer
    if len(out) > max_chars:  # safety net: cut at a line boundary
        out = out[:max_chars]
        nl = out.rfind("\n")
        if nl > max_chars * 0.8:
            out = out[:nl]
    return out


def _decode_result_url(href: str) -> str:
    try:
        u = urllib.parse.urlparse(href)
        if "duckduckgo.com" in (u.netloc or "") and u.path.startswith("/l/"):
            q = urllib.parse.parse_qs(u.query).get("uddg")
            if q:
                return q[0]
        if "bing.com" in (u.netloc or "") and u.path.startswith("/ck/"):
            q = urllib.parse.parse_qs(u.query).get("u")
            if q and q[0].startswith("a1"):
                import base64

                raw = q[0][2:]
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
    except Exception:
        pass
    return href


def normalize_url(url: Any, allow_files: bool | None = None) -> str:
    """Validate/normalize a URL coming from the model. Raises ValueError on garbage."""
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    u = url.strip().strip("\"'<>`").strip()
    if not u:
        raise ValueError("url is empty")
    if len(u) > 8000:
        raise ValueError("url is too long")
    if any(c in u for c in "\r\n\t\x00"):
        u = re.sub(r"[\r\n\t\x00]", "", u)
    low = u.lower()
    if low.startswith("file:"):
        allow = _CONFIG.get("allow_files") if allow_files is None else allow_files
        if not allow:
            raise ValueError("file: URLs are disabled (start the server with --allow-local-files "
                             "or set BROWSER_MCP_ALLOW_FILE=1)")
        return u
    if low.startswith(("about:", "data:")):
        return u
    if low.startswith(("javascript:", "chrome:", "chrome-extension:", "devtools:", "view-source:", "vbscript:")):
        raise ValueError(f"scheme not allowed: {u.split(':', 1)[0]}")
    if low.startswith("//"):
        u = "https:" + u
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
        if re.match(r"^(localhost|\d{1,3}(\.\d{1,3}){3})(:\d+)?(/|$)", u):
            u = "http://" + u
        else:
            u = "https://" + u
    p = urllib.parse.urlsplit(u)
    if p.scheme.lower() not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {p.scheme}")
    host = p.hostname or ""
    if not host or " " in host or not re.match(r"^[A-Za-z0-9.\-\[\]:_%\u0080-\uffff]+$", host):
        raise ValueError(f"invalid host in URL: {url!r}")
    if "." not in host and host != "localhost" and not host.startswith("["):
        raise ValueError(f"invalid host in URL: {url!r}")
    try:
        _ = p.port
    except ValueError:
        raise ValueError(f"invalid port in URL: {url!r}") from None
    return u


def parse_id(element_id: Any) -> int:
    if isinstance(element_id, bool):
        raise ValueError("element id must be a number like 5")
    if isinstance(element_id, int):
        return element_id
    if isinstance(element_id, float):
        if element_id.is_integer():
            return int(element_id)
        raise ValueError(f"invalid element id {element_id!r}; use a whole number from the [id] list")
    m = re.search(r"\d+", str(element_id or ""))
    if not m:
        raise ValueError(f"invalid element id {element_id!r}; use a number from the [id] list")
    return int(m.group())


# --------------------------------------------------------------------------- browser session


class Browser:
    def __init__(self, headed: bool = False, path: str | None = None, max_chars: int = MAX_CHARS,
                 lang: str | None = None):
        self.headed = headed
        self.lang = normalize_lang(lang) or system_lang()
        self.path = path
        self.max_chars = max_chars
        self.proc: subprocess.Popen | None = None
        self.tmp: str | None = None
        self.port: int | None = None
        self.cdp: CDP | None = None
        self.target_id: str | None = None
        self.frame_id: str | None = None
        self.user_agent: str | None = None
        self.last_url: str | None = None
        self.failed_url: str | None = None
        self.launches = 0
        self._dialog_tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- lifecycle
    def alive(self) -> bool:
        return (self.proc is not None and self.proc.poll() is None and self.cdp is not None
                and not self.cdp.closed)

    def _args(self, exe: str) -> list[str]:
        args = [
            exe,
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.tmp}",
            "--no-first-run", "--no-default-browser-check", "--disable-default-apps",
            "--disable-extensions", "--disable-sync", "--disable-translate",
            "--disable-background-networking", "--disable-component-update",
            "--disable-features=Translate,MediaRouter,OptimizationHints,DialMediaRouteProvider",
            "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            "--password-store=basic", "--use-mock-keychain", "--mute-audio",
            "--window-size=1280,1000", f"--lang={self.lang}",
        ]
        headed = self.headed
        if headed and IS_LINUX and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            log.warning("--headed requested but no DISPLAY/WAYLAND_DISPLAY; falling back to headless")
            headed = False
        if not headed:
            args += ["--headless=new", "--hide-scrollbars"]
        if IS_LINUX:
            root = hasattr(os, "geteuid") and os.geteuid() == 0
            container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv") or bool(
                os.environ.get("container") or os.environ.get("CI"))
            if root or container or os.environ.get("BROWSER_MCP_NO_SANDBOX"):
                args.append("--no-sandbox")
            args.append("--disable-dev-shm-usage")
            if not headed:
                args.append("--disable-gpu")
        args.append("about:blank")
        return args

    async def launch(self) -> None:
        exe = find_browser(self.path)
        if not exe:
            raise RuntimeError("No Chrome/Edge/Brave/Chromium found. Install one or pass --browser PATH "
                               "(or set BROWSER_MCP_PATH).")
        last_err: Exception | None = None
        for _attempt in range(3):
            try:
                await self._launch_once(exe)
                self.launches += 1
                return
            except Exception as e:  # port race, slow start, crash on boot...
                last_err = e
                log.warning("browser launch failed: %s", e)
                await self.close()
        raise RuntimeError(f"Could not start browser {exe}: {last_err}")

    async def _launch_once(self, exe: str) -> None:
        self.tmp = tempfile.mkdtemp(prefix=TMP_PREFIX)
        with open(os.path.join(self.tmp, "owner.pid"), "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        self.port = free_port()
        kw: dict[str, Any] = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if IS_WIN:
            kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kw["start_new_session"] = True
            if IS_LINUX:
                kw["preexec_fn"] = _child_preexec
        with _LIVE_LOCK:
            self.proc = subprocess.Popen(self._args(exe), **kw)
            _LIVE.add(self)
        base = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 30
        version = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"browser exited with code {self.proc.returncode}")
            try:
                version = await asyncio.to_thread(_http_json, base + "/json/version", "GET", 2.0)
                break
            except Exception:
                await asyncio.sleep(0.1)
        if not version:
            raise RuntimeError("DevTools endpoint did not come up")
        ua = str(version.get("User-Agent") or "")
        self.user_agent = ua.replace("HeadlessChrome", "Chrome") if ua else None
        target = None
        for _ in range(50):
            targets = await asyncio.to_thread(_http_json, base + "/json/list", "GET", 3.0)
            pages = [t for t in targets or [] if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                target = pages[0]
                break
            await asyncio.sleep(0.1)
        if not target:
            target = await asyncio.to_thread(_http_json, base + "/json/new?about:blank", "PUT", 5.0)
        self.target_id = target["id"]
        self.cdp = await CDP.connect(target["webSocketDebuggerUrl"])
        self.cdp.on_event = self._on_event
        await self.cdp.send("Page.enable")
        tree = await self.cdp.send("Page.getFrameTree")
        self.frame_id = tree["frameTree"]["frame"]["id"]
        await self.cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": INIT_JS})
        if self.user_agent:
            with contextlib.suppress(CDPError):
                await self.cdp.send("Emulation.setUserAgentOverride",
                                    {"userAgent": self.user_agent, "acceptLanguage": accept_language(self.lang)})
        with contextlib.suppress(CDPError):
            await self.cdp.send("Page.setDownloadBehavior", {"behavior": "deny"})

    def _on_event(self, method: str, params: dict) -> None:
        if method == "Page.javascriptDialogOpening" and self.cdp is not None:
            # Auto-accept alert/confirm/prompt/beforeunload so the page never blocks.
            t = asyncio.ensure_future(self.cdp.send("Page.handleJavaScriptDialog", {"accept": True}, timeout=5))
            self._dialog_tasks.add(t)
            t.add_done_callback(lambda f: (self._dialog_tasks.discard(f), f.cancelled() or f.exception()))
        elif method == "Page.frameNavigated":
            fr = params.get("frame") or {}
            if not fr.get("parentId") and fr.get("id"):
                self.frame_id = fr["id"]
        elif method == "Inspector.detached" or method == "Inspector.targetCrashed":
            if self.cdp is not None:
                asyncio.ensure_future(self.cdp.close())

    def kill_sync(self) -> None:
        """Kill the browser process tree and remove its temp profile. Safe to call many times."""
        proc, tmp = self.proc, self.tmp
        self.proc, self.tmp = None, None
        if proc is not None:
            try:
                if proc.poll() is None:
                    if IS_WIN:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
                    else:
                        with contextlib.suppress(Exception):
                            os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            pass
                if not IS_WIN:
                    with contextlib.suppress(Exception):
                        os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=5)
            except Exception:
                pass
            if not IS_WIN:  # reap helpers of that process group adopted by us
                end = time.monotonic() + 1.0
                while time.monotonic() < end:
                    try:
                        pid, _ = os.waitpid(-proc.pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    except OSError:
                        break
                    if pid == 0:
                        time.sleep(0.02)
        with _LIVE_LOCK:
            _LIVE.discard(self)
        _reap_zombies()
        _rmtree(tmp)

    async def close(self) -> None:
        cdp, self.cdp = self.cdp, None
        if cdp is not None:
            with contextlib.suppress(Exception):
                await cdp.close()
        await asyncio.to_thread(self.kill_sync)

    async def ensure(self) -> bool:
        """Make sure a healthy browser is running. Returns True if it was (re)started."""
        if self.alive():
            return False
        had = self.launches > 0
        await self.close()
        await self.launch()
        if had and self.last_url:
            with contextlib.suppress(Exception):
                await self.navigate(self.last_url)
        return True

    # ---------------------------------------------------------------- primitives
    async def eval(self, expr: str, timeout: float = 15.0) -> Any:
        assert self.cdp is not None
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = await self.cdp.send("Runtime.evaluate", {
                    "expression": expr, "returnByValue": True, "awaitPromise": True,
                    "userGesture": True, "timeout": int(timeout * 1000)}, timeout=timeout + 5)
            except CDPError as e:
                if isinstance(e, CDPTimeout):
                    raise
                last = e  # e.g. context destroyed during navigation
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            if "exceptionDetails" in r:
                d = r["exceptionDetails"]
                msg = (d.get("exception") or {}).get("description") or d.get("text") or "error"
                if "context" in msg.lower() and attempt < 2:
                    await asyncio.sleep(0.3)
                    continue
                raise CDPError(f"page script error: {msg[:300]}")
            return (r.get("result") or {}).get("value")
        raise CDPError(f"evaluate failed: {last}")

    def _main(self, p: dict) -> bool:
        return p.get("frameId") == self.frame_id

    async def _settle(self, max_wait: float = 2.0) -> None:
        """Wait until the DOM stops changing (or max_wait)."""
        js = ("(()=>{const b=document.body;return [document.readyState, b?b.getElementsByTagName('*').length:0,"
              "b?(b.textContent||'').length:0]})()")
        prev = None
        end = time.monotonic() + max_wait
        while time.monotonic() < end:
            try:
                cur = await self.eval(js, timeout=3)
            except CDPTimeout:
                raise PageHung("the page is not responding (busy JavaScript)") from None
            except CDPError:
                cur = None
            if cur and cur[0] != "loading" and cur == prev:
                return
            prev = cur
            await asyncio.sleep(0.15)

    async def _watch_nav(self, action: Callable[[], Any], grace: float = 1.2,
                         timeout: float = NAV_TIMEOUT) -> tuple[Any, bool]:
        """Run action(); if it starts a navigation, wait for the page to load. Returns (result, navigated)."""
        assert self.cdp is not None
        cdp = self.cdp
        started = cdp.waiter("Page.frameStartedLoading", self._main)
        within = cdp.waiter("Page.navigatedWithinDocument", self._main)
        stopped = cdp.waiter("Page.frameStoppedLoading", self._main)
        loaded = cdp.waiter("Page.loadEventFired")
        try:
            result = await action()
            navigated = False
            end = time.monotonic() + grace
            while time.monotonic() < end and not (started.done() or within.done()):
                await asyncio.sleep(0.05)
            if started.done():
                navigated = True
                rest = max(1.0, timeout)
                futs = [asyncio.ensure_future(stopped.wait(rest)), asyncio.ensure_future(loaded.wait(rest))]
                _, pending = await asyncio.wait(futs, return_when=asyncio.FIRST_COMPLETED)
                for f in pending:
                    f.cancel()
                if not (stopped.done() or loaded.done()):
                    with contextlib.suppress(Exception):
                        await cdp.send("Page.stopLoading", timeout=5)
            elif within.done():
                navigated = True
            await self._settle(2.5 if navigated else 1.0)
            return result, navigated
        finally:
            for w in (started, within, stopped, loaded):
                w.cancel()

    async def navigate(self, url: str) -> str | None:
        """Navigate the tab. Returns an error string for network-level failures."""
        assert self.cdp is not None
        cdp = self.cdp
        stopped = cdp.waiter("Page.frameStoppedLoading", self._main)
        loaded = cdp.waiter("Page.loadEventFired")
        err = None
        try:
            try:
                res = await cdp.send("Page.navigate", {"url": url, "transitionType": "typed"}, timeout=NAV_TIMEOUT)
            except CDPTimeout:
                res = {"errorText": None, "loaderId": "timeout"}
                with contextlib.suppress(Exception):
                    await cdp.send("Page.stopLoading", timeout=5)
            err = res.get("errorText")
            self.failed_url = url if err else None
            if res.get("loaderId") and res.get("loaderId") != "timeout":
                fut = [asyncio.ensure_future(stopped.wait(NAV_TIMEOUT)), asyncio.ensure_future(loaded.wait(NAV_TIMEOUT))]
                _, pending = await asyncio.wait(fut, return_when=asyncio.FIRST_COMPLETED)
                for f in pending:
                    f.cancel()
                if not (stopped.done() or loaded.done()):
                    with contextlib.suppress(Exception):
                        await cdp.send("Page.stopLoading", timeout=5)
        finally:
            stopped.cancel()
            loaded.cancel()
        await self._settle(2.5)
        if not err:
            with contextlib.suppress(Exception):
                self.last_url = await self.eval("location.href", timeout=5) or url
        return err

    async def snapshot(self, note: str = "") -> str:
        data = await self.eval(SNAPSHOT_JS, timeout=20)
        if isinstance(data, dict) and str(data.get("url", "")).startswith("chrome-error:"):
            real = self.failed_url or ""
            with contextlib.suppress(Exception):
                assert self.cdp is not None
                h = await self.cdp.send("Page.getNavigationHistory", timeout=5)
                real = h["entries"][h["currentIndex"]].get("url") or real
            data["url"] = real + " (failed to load)"
        out = compose(data or {}, self.max_chars, note)
        if isinstance(data, dict) and data.get("url", "").startswith(("http", "file")):
            self.last_url = data["url"]
        return out

    async def _adopt_popups(self) -> str | None:
        """Close tabs opened by the page; return the URL of the newest one so it can be opened here."""
        try:
            targets = await asyncio.to_thread(_http_json, f"http://127.0.0.1:{self.port}/json/list", "GET", 3.0)
        except Exception:
            return None
        url = None
        for t in targets or []:
            if t.get("type") == "page" and t.get("id") != self.target_id:
                u = t.get("url") or ""
                if u and not u.startswith(("about:", "chrome", "devtools")):
                    url = u
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(_http_text, f"http://127.0.0.1:{self.port}/json/close/{t['id']}", "GET", 3.0)
        return url

    # ---------------------------------------------------------------- actions
    async def open_url(self, url: str) -> str:
        err = await self.navigate(url)
        if err:
            note = f"Error: could not load {url}: {err}"
            with contextlib.suppress(Exception):
                return await self.snapshot(note)
            return note
        return await self.snapshot()

    async def click(self, eid: int) -> str:
        info_box: dict = {}

        async def act():
            info = await self.eval(f"({CLICK_JS})({eid})")
            info_box.update(info or {})
            if info and info.get("ok") and info.get("mode") == "mouse":
                x, y = float(info["x"]), float(info["y"])
                assert self.cdp is not None
                await self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
                await self.cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                                                 "button": "left", "buttons": 1, "clickCount": 1})
                await self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                                                 "button": "left", "buttons": 0, "clickCount": 1})

        _, navigated = await self._watch_nav(act)
        if not info_box.get("ok"):
            reason = info_box.get("reason", "unknown")
            msg = {"notfound": f"Error: element [{eid}] not found (ids change after every action). Use an id from this list:",
                   "disabled": f"Error: element [{eid}] is disabled."}.get(reason, f"Error: could not click [{eid}]: {reason}")
            return await self.snapshot(msg)
        popup = await self._adopt_popups()
        if popup and not navigated:
            await self.navigate(popup)
        desc = info_box.get("desc") or ""
        return await self.snapshot(f"Clicked [{eid}]" + (f" {desc!r}" if desc else "") + ".")

    async def type_text(self, eid: int, text: str, submit: bool = False, clear: bool = True) -> str:
        assert self.cdp is not None
        info = await self.eval(f"({FOCUS_JS})({eid}, {json.dumps(bool(clear))})")
        if not info or not info.get("ok"):
            reason = (info or {}).get("reason", "unknown")
            if reason == "notfound":
                return await self.snapshot(f"Error: element [{eid}] not found (ids change after every action). "
                                           "Use an id from this list:")
            if reason == "noteditable":
                return await self.snapshot(f"Error: element [{eid}] ({(info or {}).get('kind', '?')}) is not a text "
                                           "field. Pick an input/textarea id from the list:")
            return await self.snapshot(f"Error: cannot type into [{eid}]: {reason}")
        if info.get("kind") == "select":
            r = await self.eval(f"({SELECT_JS})({eid}, {json.dumps(text)})")
            if not r or not r.get("ok"):
                opts = ", ".join((r or {}).get("options") or [])
                return await self.snapshot(f"Error: no option matching {text!r} in [{eid}]. Options: {_cut(opts, 400)}")
            note = f"Selected {r.get('selected')!r} in [{eid}]."
            return await self.snapshot(note)
        if clear and info.get("before"):
            # delete the selected old content
            await self.cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Backspace", "code": "Backspace",
                                                           "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
            await self.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace",
                                                           "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
        if text:
            await self.cdp.send("Input.insertText", {"text": text})
        mode = "keyboard"
        if text:
            v = await self.eval(f"({VERIFY_JS})({json.dumps(text)}, {json.dumps(bool(clear))})")
            mode = (v or {}).get("mode", "?")
            if not v or not v.get("ok"):
                if info.get("type") in ("number", "date", "time", "month", "week", "datetime-local", "email"):
                    note = f"Warning: field [{eid}] ({info.get('type')}) rejected {text!r}."
                else:
                    note = f"Warning: text may not have been accepted by [{eid}]."
                return await self.snapshot(note)
        note = f"Typed {_cut(text, 60)!r} into [{eid}]" + (" (fallback)" if mode == "setter" else "") + "."
        if not submit:
            await self._settle(0.8)
            return await self.snapshot(note)

        async def press_enter():
            assert self.cdp is not None
            base = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
            await self.cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": "\r", "unmodifiedText": "\r", **base})
            await self.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})

        _, navigated = await self._watch_nav(press_enter, grace=1.5)
        if not navigated:
            async def fallback():
                return await self.eval(SUBMIT_FALLBACK_JS, timeout=5)

            with contextlib.suppress(CDPError):
                r, navigated = await self._watch_nav(fallback, grace=1.5)
                if r == "submitted":
                    note += " Submitted form."
        popup = await self._adopt_popups()
        if popup and not navigated:
            await self.navigate(popup)
        return await self.snapshot(note + " Pressed Enter.")

    async def scroll(self, direction: str) -> str:
        r = await self.eval(f"({SCROLL_JS})({json.dumps(direction)})")
        await self._settle(1.0)
        note = ""
        if r:
            if r.get("before") == r.get("after"):
                edge = "bottom" if direction in ("down", "bottom") else "top"
                note = f"Already at the {edge} of the page."
            else:
                note = f"Scrolled {direction}."
        return await self.snapshot(note)

    async def go_back(self) -> str:
        assert self.cdp is not None
        hist = await self.cdp.send("Page.getNavigationHistory")
        idx = int(hist.get("currentIndex", 0))
        entries = hist.get("entries") or []
        if idx <= 0 or idx > len(entries) - 1:
            return await self.snapshot("Error: no previous page in history.")
        entry = entries[idx - 1]

        async def act():
            assert self.cdp is not None
            await self.cdp.send("Page.navigateToHistoryEntry", {"entryId": entry["id"]})

        await self._watch_nav(act, grace=2.0)
        return await self.snapshot("Went back.")

    async def search(self, query: str) -> str:
        q = urllib.parse.quote_plus(query)
        engines = [
            ("DuckDuckGo", f"https://html.duckduckgo.com/html/?q={q}"),
            ("DuckDuckGo Lite", f"https://lite.duckduckgo.com/lite/?q={q}"),
            ("Bing", f"https://www.bing.com/search?q={q}"),
        ]
        errors = []
        for name, url in engines:
            err = await self.navigate(url)
            if err:
                errors.append(f"{name}: {err}")
                continue
            r = await self.eval(SEARCH_JS, timeout=15) or {}
            results = r.get("results") or []
            if not results:
                if r.get("noresults") and not r.get("blocked"):
                    return f'No results for "{query}" ({name}).'
                errors.append(f"{name}: {'blocked by captcha' if r.get('blocked') else 'no results parsed'}")
                continue
            lines = [f'Search results for "{_cut(query, 100)}" ({name}). Use open_url(url) or click(id):']
            budget = self.max_chars - len(lines[0]) - 10
            for it in results:
                url_ = _decode_result_url(str(it.get("href") or ""))
                block = f"[{it.get('id')}] {_cut(str(it.get('title') or ''), 120)}\n    {_cut(url_, 200)}"
                snip = str(it.get("snippet") or "")
                if snip:
                    block += f"\n    {_cut(snip, 220)}"
                if len(block) + 1 > budget:
                    break
                lines.append(block)
                budget -= len(block) + 1
            return "\n".join(lines)
        # Every engine failed: show whatever page we are on.
        return await self.snapshot("Search failed (" + "; ".join(errors)[:300] + "). Current page:")


# --------------------------------------------------------------------------- MCP server

_browser: Browser | None = None
_lock: asyncio.Lock | None = None
_CONFIG: dict[str, Any] = {"headed": False, "path": None, "max_chars": MAX_CHARS, "lang": None,
                           "allow_files": os.environ.get("BROWSER_MCP_ALLOW_FILE", "").strip().lower()
                           in ("1", "true", "yes", "on")}


def get_browser() -> Browser:
    global _browser
    if _browser is None:
        _browser = Browser(headed=_CONFIG["headed"], path=_CONFIG["path"], max_chars=_CONFIG["max_chars"],
                           lang=_CONFIG["lang"])
    return _browser


def clip(text: str, max_chars: int) -> str:
    """Hard output limit: cut at a line boundary when possible."""
    if len(text) <= max_chars:
        return text
    out = text[: max(0, max_chars - 1)]
    nl = out.rfind("\n")
    if nl > max_chars * 0.8:
        out = out[:nl]
    return out + "\u2026"


async def run_tool(name: str, fn: Callable[[Browser], Any], retry: bool = True) -> str:
    """Serialize tool calls, (re)start the browser as needed and never raise into the MCP layer.
    The returned text never exceeds the configured max_chars (including any restart note)."""
    out = await _run_tool(name, fn, retry)
    return clip(out, int(_CONFIG.get("max_chars") or MAX_CHARS))


async def _run_tool(name: str, fn: Callable[[Browser], Any], retry: bool = True) -> str:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        b = get_browser()
        note = ""
        for attempt in range(2):
            try:
                restarted = await b.ensure()
                if restarted and b.launches > 1:
                    note = "(Browser was restarted.) "
                if note:  # leave room for the note so the page view is not cut at the end
                    b.max_chars = max(1, int(_CONFIG.get("max_chars") or MAX_CHARS) - len(note))
                try:
                    out = await fn(b)
                finally:
                    b.max_chars = int(_CONFIG.get("max_chars") or MAX_CHARS)
                return note + out if note and not out.startswith("(") else out
            except ValueError as e:
                return f"Error: {e}"
            except CDPTimeout as e:
                log.warning("%s: page/browser not responding (%s); restarting", name, e)
                url = b.last_url
                with contextlib.suppress(Exception):
                    await b.close()
                reopened = False
                with contextlib.suppress(Exception):
                    await b.ensure()
                    reopened = bool(url) and b.alive()
                return (f"Error: the page did not respond in time ({e}). The browser was restarted"
                        + (f" and {url} reopened" if reopened else "") + "; try another page or action.")
            except (ConnectionError, OSError) as e:
                log.warning("%s: browser connection problem (%s); restarting", name, e)
                with contextlib.suppress(Exception):
                    await b.close()
                if attempt == 0 and retry:
                    note = "(Browser was restarted.) "
                    continue
                return f"Error: browser problem during {name}: {e}. The browser was restarted; try again."
            except RuntimeError as e:
                return f"Error: {e}"
            except Exception as e:  # never crash the server
                log.exception("%s failed", name)
                return f"Error: {name} failed: {type(e).__name__}: {_cut(str(e), 300)}"
        return f"Error: {name} failed"


def _check_text(v: Any, what: str, max_len: int = 5000) -> str:
    if v is None:
        raise ValueError(f"{what} is required")
    s = str(v)
    if len(s) > max_len:
        raise ValueError(f"{what} is too long (max {max_len} chars)")
    return s


async def tool_web_search(query: str) -> str:
    try:
        q = _check_text(query, "query", 500).strip()
        if not q:
            raise ValueError("query is empty")
    except ValueError as e:
        return clip(f"Error: {e}", int(_CONFIG.get("max_chars") or MAX_CHARS))
    return await run_tool("web_search", lambda b: b.search(q))


async def tool_open_url(url: str) -> str:
    try:
        u = normalize_url(url)
    except ValueError as e:
        return clip(f"Error: {e}", int(_CONFIG.get("max_chars") or MAX_CHARS))
    return await run_tool("open_url", lambda b: b.open_url(u))


async def tool_click(element_id: Any) -> str:
    try:
        eid = parse_id(element_id)
    except ValueError as e:
        return clip(f"Error: {e}", int(_CONFIG.get("max_chars") or MAX_CHARS))
    return await run_tool("click", lambda b: b.click(eid), retry=False)


async def tool_type_text(element_id: Any, text: str, submit: bool = False) -> str:
    try:
        eid = parse_id(element_id)
        t = _check_text(text, "text", 20000)
    except ValueError as e:
        return clip(f"Error: {e}", int(_CONFIG.get("max_chars") or MAX_CHARS))
    t = t.replace("\r\n", "\n")
    sub = bool(submit) if not isinstance(submit, str) else submit.strip().lower() in ("1", "true", "yes", "y")
    if t.endswith("\n") and not t.endswith("\n\n"):
        t, sub = t[:-1], True
    return await run_tool("type_text", lambda b: b.type_text(eid, t, sub), retry=False)


_DIRS = {"down": "down", "d": "down", "next": "down", "pagedown": "down", "page down": "down", "forward": "down",
         "up": "up", "u": "up", "pageup": "up", "page up": "up", "prev": "up", "previous": "up", "back": "up",
         "top": "top", "start": "top", "home": "top", "beginning": "top",
         "bottom": "bottom", "end": "bottom"}


async def tool_scroll(direction: str = "down") -> str:
    d = _DIRS.get(str(direction or "down").strip().lower())
    if d is None:
        return clip("Error: direction must be 'down', 'up', 'top' or 'bottom'",
                    int(_CONFIG.get("max_chars") or MAX_CHARS))
    return await run_tool("scroll", lambda b: b.scroll(d))


async def tool_go_back() -> str:
    return await run_tool("go_back", lambda b: b.go_back(), retry=False)


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("capybrowser")

    @mcp.tool()
    async def web_search(query: str) -> str:
        """Search the web. Returns numbered results with title, URL and snippet.
        Next, call open_url with a result URL (or click with its id)."""
        return await tool_web_search(query)

    @mcp.tool()
    async def open_url(url: str) -> str:
        """Open a web page. Returns the page text and a list of clickable elements, each with an [id]."""
        return await tool_open_url(url)

    @mcp.tool()
    async def click(element_id: int) -> str:
        """Click the element with this [id] (link, button, checkbox...) from the latest page view."""
        return await tool_click(element_id)

    @mcp.tool()
    async def type_text(element_id: int, text: str, submit: bool = False) -> str:
        """Type text into the input field with this [id] (replaces its content; also picks options in a select).
        Set submit=true to press Enter afterwards (e.g. to run a search)."""
        return await tool_type_text(element_id, text, submit)

    @mcp.tool()
    async def scroll(direction: str = "down") -> str:
        """Scroll the page: 'down', 'up', 'top' or 'bottom'. Returns the newly visible content."""
        return await tool_scroll(direction)

    @mcp.tool()
    async def go_back() -> str:
        """Go back to the previous page in the browser history."""
        return await tool_go_back()

    return mcp


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        cleanup_all()
        os._exit(128 + int(signum))

    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK", "SIGQUIT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(Exception):
                signal.signal(sig, handler)


def setup(headed: bool = False, path: str | None = None, max_chars: int = MAX_CHARS, lang: str | None = None,
          allow_files: bool | None = None) -> None:
    _CONFIG.update(headed=headed, path=path, max_chars=max_chars, lang=lang)
    if allow_files is not None:
        _CONFIG["allow_files"] = bool(allow_files) or _CONFIG["allow_files"]
    atexit.register(cleanup_all)
    if IS_LINUX:
        _linux_prctl(36, 1)  # PR_SET_CHILD_SUBREAPER: adopt and reap orphaned browser helpers
    cleanup_stale_dirs()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="capybrowser",
                                 description="CapyBrowser: MCP server that drives the system Chromium-based browser over CDP")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--headed", action="store_true", help="show the browser window (debugging)")
    ap.add_argument("--browser", default=None, help="path to the browser executable")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS, help="max characters per tool response")
    ap.add_argument("--lang", default=None, help="browser UI/Accept-Language, e.g. ru-RU (default: system locale)")
    ap.add_argument("--allow-local-files", action="store_true", help="allow open_url with file:// URLs")
    ap.add_argument("--log-level", default=os.environ.get("BROWSER_MCP_LOG", "WARNING"))
    args, _ = ap.parse_known_args(argv)
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    setup(args.headed, args.browser, args.max_chars, args.lang, args.allow_local_files)
    _install_signal_handlers()
    try:
        build_server().run("stdio")
    finally:
        cleanup_all()


if __name__ == "__main__":
    main()
