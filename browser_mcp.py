#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.13.0,<2",
#     "websockets>=13.0",
#     "pyyaml>=6.0",
#     "httpx>=0.27",
# ]
# ///
"""CapyBrowser (browser_mcp.py) - a single-file MCP server that lets an LLM drive the system browser.

Talks to Chrome / Edge / Brave / Chromium directly over the Chrome DevTools Protocol
(WebSocket). No Playwright, Selenium or Puppeteer.
https://github.com/capyriver/CapyBrowser

    uv run browser_mcp.py            # headless (default)
    uv run browser_mcp.py --headed   # visible window, for debugging
    uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser
    capybrowser --config capy.yaml   # browser settings + extra MCP servers ("connectors") from one file
    capybrowser init | check         # create / validate capy.yaml

Options: --browser PATH (or env BROWSER_MCP_PATH), --max-chars N (default 3500),
--lang ru-RU (or env BROWSER_MCP_LANG; default: system locale),
--allow-local-files (or env BROWSER_MCP_ALLOW_FILE=1) to permit file:// URLs.
Tools: web_search, open_url, click, type_text, scroll, go_back
       + list_connectors, find_tools, call_tool for the MCP servers listed in capy.yaml.
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

__version__ = "1.3.0"

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
    with contextlib.suppress(Exception):
        kill_connector_processes()


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
                 lang: str | None = None, profile_dir: str | None = None):
        self.headed = headed
        # Persistent profile (kept between runs) or None for a temporary one that is always deleted.
        self.profile_dir = os.path.abspath(os.path.expanduser(profile_dir)) if profile_dir else None
        self.user_data_dir: str | None = None
        self.browser_ws: str | None = None
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
            f"--user-data-dir={self.user_data_dir}",
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
        if self.profile_dir:
            os.makedirs(self.profile_dir, exist_ok=True)
            self.user_data_dir = self.profile_dir
        else:
            self.tmp = tempfile.mkdtemp(prefix=TMP_PREFIX)
            with open(os.path.join(self.tmp, "owner.pid"), "w", encoding="ascii") as f:
                f.write(str(os.getpid()))
            self.user_data_dir = self.tmp
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
        self.browser_ws = version.get("webSocketDebuggerUrl")
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
        if self.profile_dir and self.proc is not None and self.proc.poll() is None and self.browser_ws:
            # persistent profile: let Chrome flush cookies/localStorage to disk before it exits
            with contextlib.suppress(Exception):
                bcdp = await CDP.connect(self.browser_ws)
                with contextlib.suppress(Exception):
                    await bcdp.send("Browser.close", timeout=5)
                await bcdp.close()
                proc = self.proc
                await asyncio.to_thread(proc.wait, 10)
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
                         timeout: float | None = None) -> tuple[Any, bool]:
        """Run action(); if it starts a navigation, wait for the page to load. Returns (result, navigated)."""
        timeout = NAV_TIMEOUT if timeout is None else timeout
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
_CONFIG: dict[str, Any] = {"headed": False, "path": None, "max_chars": MAX_CHARS, "lang": None, "profile_dir": None,
                           "allow_files": os.environ.get("BROWSER_MCP_ALLOW_FILE", "").strip().lower()
                           in ("1", "true", "yes", "on")}


def get_browser() -> Browser:
    global _browser
    if _browser is None:
        _browser = Browser(headed=_CONFIG["headed"], path=_CONFIG["path"], max_chars=_CONFIG["max_chars"],
                           lang=_CONFIG["lang"], profile_dir=_CONFIG.get("profile_dir"))
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
    await _reload_if_needed()
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

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any):
        try:
            yield {}
        finally:
            await ROUTER.close_all()
            kill_connector_processes()

    mcp = FastMCP("capybrowser", lifespan=lifespan)
    mcp._mcp_server.version = __version__

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

    @mcp.tool()
    async def list_connectors() -> str:
        """List the extra MCP connectors from capy.yaml (mail, files, GitHub...) and their status."""
        return await tool_list_connectors()

    @mcp.tool()
    async def find_tools(query: str = "", connector: str | None = None, limit: int = 8) -> str:
        """Find connector tools by what you need (English or Russian, e.g. 'send email', 'задачи').
        Returns tool names with arguments; then use call_tool."""
        return await tool_find_tools(query, connector, limit)

    @mcp.tool()
    async def call_tool(tool: str, arguments: dict | str | None = None) -> str:
        """Run a connector tool found with find_tools. tool = 'connector.tool', arguments = JSON object."""
        return await tool_call_tool(tool, arguments)

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
          allow_files: bool | None = None, profile_dir: str | None = None, nav_timeout: float | None = None) -> None:
    global NAV_TIMEOUT
    _CONFIG.update(headed=headed, path=path, max_chars=max_chars, lang=lang, profile_dir=profile_dir)
    if nav_timeout:
        NAV_TIMEOUT = float(nav_timeout)
    if allow_files is not None:
        _CONFIG["allow_files"] = bool(allow_files) or _CONFIG["allow_files"]
    atexit.register(cleanup_all)
    if IS_LINUX:
        _linux_prctl(36, 1)  # PR_SET_CHILD_SUBREAPER: adopt and reap orphaned browser helpers
    cleanup_stale_dirs()


# --------------------------------------------------------------------------- secrets

_SECRET_VALUES: set[str] = set()
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_\-]{30,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}|\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}\b|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})")
_AUTH_RE = re.compile(r"(?i)\b(bearer|basic)(\s+)([A-Za-z0-9._~+/\-]{8,}=*)")


def register_secret(value: Any) -> None:
    """Remember a configured secret so it is never echoed back to the model or written to logs."""
    if isinstance(value, str) and len(value.strip()) >= 6:
        _SECRET_VALUES.add(value.strip())


def mask_secrets(text: Any) -> str:
    text = "" if text is None else str(text)
    for value in sorted(_SECRET_VALUES, key=len, reverse=True):
        if value in text:
            text = text.replace(value, "***")
    text = _SECRET_RE.sub("***", text)
    return _AUTH_RE.sub(lambda m: m.group(1) + m.group(2) + "***", text)


class _MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        with contextlib.suppress(Exception):
            record.msg = mask_secrets(record.getMessage())
            record.args = ()
        return True


# --------------------------------------------------------------------------- capy.yaml

CAPY_YAML_TEMPLATE = """\
# capy.yaml - CapyBrowser config: browser settings + extra MCP servers ("connectors").
# Your AI app starts only CapyBrowser. The model gets the 6 browser tools plus three
# router tools (list_connectors, find_tools, call_tool) that reach every connector below.
# The file is re-read automatically when it changes. Docs:
# https://github.com/capyriver/CapyBrowser#capyyaml

browser:
  headed: false              # true = show the browser window
  lang: en-US                # browser language; remove to use the system locale
  max_chars: 3500            # max size of any tool response
  nav_timeout: 25            # seconds to wait for a page to load
  allow_local_files: false   # allow file:// URLs
  # profile_dir: ~/.capybrowser/profile   # keep cookies/logins between runs (default: temporary profile)
  # browser: /path/to/chrome              # a specific browser executable

# env_file: .env             # KEY=VALUE file next to capy.yaml (loaded automatically if present)

mcp_servers: {}              # connectors; examples (remove "{}" above when you add one):
#  time:                     # local stdio server, same format as Claude Desktop
#    command: uvx
#    args: ["mcp-server-time"]
#    description: current time and time zones
#  files:
#    command: npx
#    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/folder"]
#  github:                   # remote server with a token from the environment / .env
#    type: streamable-http   # or: sse
#    url: https://api.githubcopilot.com/mcp/
#    auth:
#      type: bearer
#      token: ${GITHUB_TOKEN}
#  notion:                   # remote server with OAuth 2.0 + PKCE (opens your browser once)
#    type: streamable-http
#    url: https://mcp.notion.com/mcp
#    auth:
#      type: oauth
"""

_TOP_KEYS = {"browser", "mcp_servers", "env_file"}
_BROWSER_KEYS = {"headed", "browser", "path", "lang", "max_chars", "profile_dir", "allow_local_files", "nav_timeout"}
_SERVER_KEYS = {"type", "command", "args", "env", "cwd", "url", "headers", "auth", "description", "enabled", "timeout"}
_AUTH_KEYS = {"type", "token", "client_id", "client_secret", "scopes", "redirect_port", "client_name"}
_TYPES = {"stdio": "stdio", "streamable-http": "streamable-http", "streamable_http": "streamable-http",
          "http": "streamable-http", "sse": "sse"}


class ConfigError(ValueError):
    pass


def find_config(path: str | None) -> str | None:
    """Explicit path, then $CAPYBROWSER_CONFIG, ./capy.yaml, ~/.capy.yaml."""
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"config file not found: {path}")
        return path
    for cand in (os.environ.get("CAPYBROWSER_CONFIG"), "capy.yaml", "capy.yml",
                 os.path.join(os.path.expanduser("~"), ".capy.yaml")):
        if cand and os.path.isfile(cand):
            return cand
    return None


def load_dotenv(path: str) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE, optional 'export', quotes and # comments."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, sep, value = line.partition("=")
            key = key.strip()
            if not sep or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            else:
                value = re.sub(r"\s+#.*$", "", value)
            out[key] = value
    return out


def _expand(value: Any, env: dict[str, str], missing: set[str]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name = m.group(1)
            if name not in env:
                missing.add(name)
            return env.get(name, "")
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, value)
    if isinstance(value, list):
        return [_expand(v, env, missing) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, env, missing) for k, v in value.items()}
    return value


def _check_keys(where: str, data: dict, allowed: set[str]) -> None:
    bad = sorted(set(map(str, data)) - allowed)
    if bad:
        raise ConfigError(f"{where}: unknown option(s): {', '.join(bad)} (allowed: {', '.join(sorted(allowed))})")


def _want(where: str, value: Any, kinds: tuple, what: str) -> Any:
    if value is not None and (not isinstance(value, kinds) or (bool not in kinds and isinstance(value, bool))):
        raise ConfigError(f"{where}: must be {what}")
    return value


def _normalize_server(name: str, spec: Any) -> dict:
    where = f"mcp_servers.{name}"
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$", str(name)):
        raise ConfigError(f"{where}: name may contain only letters, digits, '-' and '_' (max 41 chars)")
    if not isinstance(spec, dict):
        raise ConfigError(f"{where}: must be a mapping (command/args or url)")
    _check_keys(where, spec, _SERVER_KEYS)
    kind = spec.get("type") or ("stdio" if spec.get("command") else "streamable-http" if spec.get("url") else None)
    if kind is None:
        raise ConfigError(f"{where}: set 'command' (local stdio server) or 'url' (remote server)")
    if str(kind).lower() not in _TYPES:
        raise ConfigError(f"{where}.type: must be stdio, streamable-http or sse")
    out: dict[str, Any] = {"type": _TYPES[str(kind).lower()]}
    out["description"] = str(_want(f"{where}.description", spec.get("description"), (str,), "a string") or "")
    out["enabled"] = bool(_want(f"{where}.enabled", spec.get("enabled", True), (bool,), "true or false"))
    timeout = _want(f"{where}.timeout", spec.get("timeout", 120), (int, float), "a number of seconds")
    if not 1 <= float(timeout) <= 3600:
        raise ConfigError(f"{where}.timeout: must be between 1 and 3600 seconds")
    out["timeout"] = float(timeout)
    if out["type"] == "stdio":
        for k in ("url", "headers", "auth"):
            if k in spec:
                raise ConfigError(f"{where}.{k}: only for remote servers (type streamable-http or sse)")
        if not isinstance(spec.get("command"), str) or not spec["command"].strip():
            raise ConfigError(f"{where}.command: required for a stdio server")
        args = spec.get("args") or []
        if not isinstance(args, list) or not all(isinstance(a, (str, int, float)) for a in args):
            raise ConfigError(f"{where}.args: must be a list of strings")
        env = spec.get("env") or {}
        if not isinstance(env, dict):
            raise ConfigError(f"{where}.env: must be a mapping NAME: value")
        out.update(command=spec["command"], args=[str(a) for a in args], env={str(k): str(v) for k, v in env.items()},
                   cwd=os.path.expanduser(str(spec["cwd"])) if spec.get("cwd") else None)
        for v in out["env"].values():
            register_secret(v)
        return out
    for k in ("command", "args", "env", "cwd"):
        if k in spec:
            raise ConfigError(f"{where}.{k}: only for stdio servers")
    url = spec.get("url")
    if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+", url):
        raise ConfigError(f"{where}.url: required, must start with http:// or https://")
    headers = spec.get("headers") or {}
    if not isinstance(headers, dict):
        raise ConfigError(f"{where}.headers: must be a mapping Header-Name: value")
    out.update(url=url, headers={str(k): str(v) for k, v in headers.items()}, auth=None)
    for k, v in out["headers"].items():
        if k.lower() in ("authorization", "cookie", "proxy-authorization") or "token" in k.lower() or "key" in k.lower():
            register_secret(v)
            register_secret(v.split(" ", 1)[-1])
    auth = spec.get("auth")
    if auth is not None:
        if not isinstance(auth, dict):
            raise ConfigError(f"{where}.auth: must be a mapping with 'type: bearer' or 'type: oauth'")
        _check_keys(f"{where}.auth", auth, _AUTH_KEYS)
        atype = str(auth.get("type") or "").lower()
        if atype == "bearer":
            token = auth.get("token")
            if not isinstance(token, str):
                raise ConfigError(f"{where}.auth.token: required for bearer auth (use ${{ENV_VAR}})")
            if not token.strip():  # e.g. ${GITHUB_TOKEN} not set: keep the config, disable only this connector
                out["problem"] = "auth.token is empty - set the environment variable used in capy.yaml (or .env)"
            register_secret(token)
            out["auth"] = {"type": "bearer", "token": token.strip()}
        elif atype == "oauth":
            scopes = auth.get("scopes") or []
            if isinstance(scopes, str):
                scopes = scopes.split()
            if not isinstance(scopes, list):
                raise ConfigError(f"{where}.auth.scopes: must be a list or a space-separated string")
            port = _want(f"{where}.auth.redirect_port", auth.get("redirect_port", 33418), (int,), "a port number")
            if not 1024 <= int(port) <= 65535:
                raise ConfigError(f"{where}.auth.redirect_port: must be between 1024 and 65535")
            register_secret(auth.get("client_secret"))
            out["auth"] = {"type": "oauth", "client_id": auth.get("client_id") or None,
                           "client_secret": auth.get("client_secret") or None, "scopes": [str(x) for x in scopes],
                           "redirect_port": int(port), "client_name": str(auth.get("client_name") or "CapyBrowser")}
        else:
            raise ConfigError(f"{where}.auth.type: must be 'bearer' or 'oauth'")
    return out


def load_config(path: str | None) -> dict:
    """Read and validate capy.yaml (+ optional .env). Always returns a usable config; with no file,
    the defaults run the built-in browser only."""
    found = find_config(path)
    if not found:
        return {"path": None, "browser": {}, "mcp_servers": {}, "warnings": [], "files": []}
    with open(found, encoding="utf-8-sig") as f:
        text = f.read()
    return parse_config_text(text, found)


def parse_config_text(text: str, found: str) -> dict:
    """Validate capy.yaml content; `found` is its (real or intended) path, used for messages and .env lookup."""
    cfg: dict[str, Any] = {"path": found, "browser": {}, "mcp_servers": {}, "warnings": [], "files": [found]}
    try:
        if found.endswith(".json"):
            data = json.loads(text or "{}")
        else:
            import yaml

            data = yaml.safe_load(text)
    except Exception as e:
        raise ConfigError(f"{found}: invalid {'JSON' if found.endswith('.json') else 'YAML'}: {e}") from None
    if data is None:
        return cfg  # empty file = defaults
    if not isinstance(data, dict):
        raise ConfigError(f"{found}: the top level must be a mapping (browser:, mcp_servers:)")
    _check_keys(found, data, _TOP_KEYS)
    env = dict(os.environ)
    env_file = data.get("env_file")
    if env_file is not None and not isinstance(env_file, str):
        raise ConfigError(f"{found}: env_file must be a path")
    env_path = os.path.join(os.path.dirname(os.path.abspath(found)), os.path.expanduser(env_file or ".env"))
    if os.path.isfile(env_path):
        cfg["files"].append(env_path)
        for k, v in load_dotenv(env_path).items():
            env.setdefault(k, v)
            register_secret(v)
    elif env_file:
        cfg["warnings"].append(f"env_file not found: {env_file}")
    missing: set[str] = set()
    data = _expand(data, env, missing)
    if missing:
        cfg["warnings"].append("environment variable(s) not set: " + ", ".join(sorted(missing)))
    browser = data.get("browser") or {}
    if not isinstance(browser, dict):
        raise ConfigError("browser: must be a mapping")
    _check_keys("browser", browser, _BROWSER_KEYS)
    b: dict[str, Any] = {}
    b["headed"] = _want("browser.headed", browser.get("headed"), (bool,), "true or false")
    b["allow_local_files"] = _want("browser.allow_local_files", browser.get("allow_local_files"), (bool,), "true or false")
    b["lang"] = _want("browser.lang", browser.get("lang"), (str,), "a language like ru-RU")
    exe = browser.get("browser", browser.get("path"))
    b["browser"] = os.path.expanduser(_want("browser.browser", exe, (str,), "a path to the browser executable")) if exe else None
    pdir = _want("browser.profile_dir", browser.get("profile_dir"), (str,), "a folder path")
    b["profile_dir"] = os.path.expanduser(pdir) if pdir else None
    mc = _want("browser.max_chars", browser.get("max_chars"), (int,), "a whole number")
    if mc is not None and not 200 <= mc <= 200000:
        raise ConfigError("browser.max_chars: must be between 200 and 200000")
    b["max_chars"] = mc
    nt = _want("browser.nav_timeout", browser.get("nav_timeout"), (int, float), "a number of seconds")
    if nt is not None and not 1 <= nt <= 600:
        raise ConfigError("browser.nav_timeout: must be between 1 and 600 seconds")
    b["nav_timeout"] = float(nt) if nt is not None else None
    cfg["browser"] = b
    servers = data.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        raise ConfigError("mcp_servers: must be a mapping of name -> server settings")
    cfg["mcp_servers"] = {str(n): _normalize_server(str(n), s) for n, s in servers.items()}
    return cfg


def _config_key(path: str | None, extra: list | None = None) -> tuple:
    files = [path] if path else []
    if path:
        files.append(os.path.join(os.path.dirname(os.path.abspath(path)), ".env"))
    files += [f for f in (extra or []) if f not in files]
    key = []
    for f in files:
        try:
            st = os.stat(f)
            key.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((f, None, None))
    return tuple(key)


# --------------------------------------------------------------------------- child processes of connectors

_CONNECTOR_PIDS: set[int] = set()


def _child_pids() -> set[int]:
    """PIDs of our direct child processes (Linux, macOS and Windows)."""
    me = os.getpid()
    out: set[int] = set()
    try:
        if IS_LINUX:
            for name in os.listdir("/proc"):
                if name.isdigit():
                    with contextlib.suppress(OSError, ValueError, IndexError):
                        with open(f"/proc/{name}/stat", encoding="ascii", errors="replace") as f:
                            stat = f.read()
                        if int(stat[stat.rindex(")") + 2:].split()[1]) == me:
                            out.add(int(name))
        elif IS_WIN:
            import ctypes
            from ctypes import wintypes

            class PE(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                            ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                            ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                            ("szExeFile", ctypes.c_wchar * 260)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
            if snap and snap != wintypes.HANDLE(-1).value:
                try:
                    pe = PE()
                    pe.dwSize = ctypes.sizeof(PE)
                    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
                    while ok:
                        if pe.th32ParentProcessID == me:
                            out.add(int(pe.th32ProcessID))
                        ok = k32.Process32NextW(snap, ctypes.byref(pe))
                finally:
                    k32.CloseHandle(snap)
        else:
            res = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True, timeout=5)
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == str(me):
                    out.add(int(parts[0]))
    except Exception:
        pass
    return out


def _kill_tree(pid: int) -> None:
    if not _pid_alive(pid):
        return
    if IS_WIN:
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        return
    with contextlib.suppress(Exception):
        pgid = os.getpgid(pid)
        if pgid == pid:  # stdio connectors run in their own session/process group
            os.killpg(pgid, signal.SIGKILL)
            return
    with contextlib.suppress(Exception):
        os.kill(pid, signal.SIGKILL)


def kill_connector_processes() -> None:
    for pid in list(_CONNECTOR_PIDS):
        _kill_tree(pid)
        _CONNECTOR_PIDS.discard(pid)
    _reap_zombies()


# --------------------------------------------------------------------------- connectors + router

_SYNONYMS = [
    {"mail", "email", "e-mail", "gmail", "outlook", "inbox", "почта", "почту", "почты", "письмо", "письма", "писем",
     "ящик", "imap", "smtp", "message"},
    {"task", "tasks", "todo", "todos", "notes", "note", "notion", "задача", "задачи", "задач", "заметка", "заметки",
     "заметок", "список", "trello", "jira", "linear", "todoist", "page", "pages", "страница"},
    {"code", "github", "git", "gitlab", "repo", "repository", "repositories", "pr", "pull", "issue", "issues", "commit",
     "код", "репозиторий", "репо", "коммит", "ветка", "branch"},
    {"file", "files", "folder", "directory", "filesystem", "fs", "path", "файл", "файлы", "файла", "папка", "папки",
     "документ", "документы", "read", "write", "прочитать", "записать"},
    {"calendar", "event", "events", "meeting", "schedule", "календарь", "встреча", "встречи", "событие", "события",
     "расписание"},
    {"time", "date", "clock", "timezone", "время", "дата", "часы", "часовой", "пояс", "сейчас", "now"},
    {"search", "find", "lookup", "query", "поиск", "найти", "искать", "найди"},
    {"database", "db", "sql", "table", "postgres", "sqlite", "mysql", "база", "базы", "таблица", "запрос"},
    {"chat", "slack", "telegram", "discord", "message", "messages", "send", "сообщение", "сообщения", "отправить",
     "чат", "написать"},
    {"weather", "погода", "погоду", "forecast", "прогноз"},
    {"memory", "remember", "память", "запомнить", "knowledge", "graph"},
    {"translate", "translation", "перевод", "перевести", "language"},
]
_SYN_INDEX: dict[str, set[str]] = {}
for _group in _SYNONYMS:
    for _w in _group:
        _SYN_INDEX.setdefault(_w, set()).update(_group)


def _words(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text or ""))
    return [w for w in re.split(r"[^0-9A-Za-zА-Яа-яЁёҚқҒғҮүҰұӘәІіҢңӨөҺһ]+", text.lower()) if w]


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def _signature(qualified: str, tool: Any, width: int = 90) -> str:
    schema = getattr(tool, "inputSchema", None) or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    args = []
    for pname, pspec in list(props.items())[:8]:
        ptype = pspec.get("type") if isinstance(pspec, dict) else None
        if isinstance(ptype, list):
            ptype = "|".join(str(t) for t in ptype if t != "null")
        if not ptype and isinstance(pspec, dict) and pspec.get("enum"):
            ptype = "|".join(map(str, pspec["enum"][:4]))
        args.append(f"{pname}{'' if pname in required else '?'}: {ptype or 'any'}")
    if len(props) > 8:
        args.append("…")
    desc = re.sub(r"\s+", " ", (getattr(tool, "description", None) or "").strip())
    return f"{qualified}({', '.join(args)})" + (f" — {_cut(desc, width)}" if desc else "")


class ConnectorError(RuntimeError):
    pass


class _FileTokenStorage:
    """OAuth tokens / client registration for one connector, stored in ~/.capybrowser/oauth (mode 600)."""

    def __init__(self, name: str, url: str, client: dict | None):
        base = os.environ.get("CAPYBROWSER_HOME") or os.path.join(os.path.expanduser("~"), ".capybrowser")
        digest = __import__("hashlib").sha256(url.encode()).hexdigest()[:10]
        self.path = os.path.join(base, "oauth", f"{name}-{digest}.json")
        self.client = client

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        data = self._read().get("tokens")
        if data:
            register_secret(data.get("access_token"))
            register_secret(data.get("refresh_token"))
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens) -> None:
        register_secret(tokens.access_token)
        register_secret(getattr(tokens, "refresh_token", None))
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        data = self._read().get("client_info") or self.client
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


class _OAuthCallback:
    """Local http://127.0.0.1:<port>/callback receiver for the authorization code (PKCE flow)."""

    def __init__(self, port: int):
        self.port = port
        self.result: tuple[str, str | None] | None = None
        self.error: str | None = None
        self.done = threading.Event()
        self.server: Any = None

    def start(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep codes out of logs
                pass

            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if urllib.parse.urlsplit(self.path).path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                if q.get("code"):
                    outer.result = (q["code"][0], (q.get("state") or [None])[0])
                    body = "Authorization complete. You can close this window and return to your AI app."
                else:
                    outer.error = (q.get("error") or ["no authorization code"])[0]
                    body = "Authorization failed. You can close this window."
                data = ("<!doctype html><meta charset=utf-8><title>CapyBrowser</title><p>" + body + "</p>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                outer.done.set()

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self.server is not None:
            with contextlib.suppress(Exception):
                self.server.shutdown()
                self.server.server_close()
            self.server = None

    async def wait(self, timeout: float = 300.0) -> tuple[str, str | None]:
        try:
            ok = await asyncio.to_thread(self.done.wait, timeout)
        finally:
            self.stop()
        if not ok:
            raise ConnectorError("OAuth sign-in timed out (no answer in the browser within 5 minutes)")
        if self.result is None:
            raise ConnectorError(f"OAuth sign-in failed: {self.error}")
        return self.result


_OAUTH_URLS: dict[str, str] = {}  # last sign-in URL per connector (shown by the UI if no browser opened)


def oauth_has_tokens(name: str, spec: dict) -> bool:
    data = _FileTokenStorage(name, spec.get("url", ""), None)._read()
    return bool((data.get("tokens") or {}).get("access_token"))


def _oauth_provider(name: str, spec: dict) -> Any:
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    auth = spec["auth"]
    redirect = f"http://127.0.0.1:{auth['redirect_port']}/callback"
    meta = OAuthClientMetadata(client_name=auth["client_name"], redirect_uris=[redirect],
                               grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                               scope=" ".join(auth["scopes"]) or None,
                               token_endpoint_auth_method="client_secret_post" if auth["client_secret"] else "none")
    preset = None
    if auth["client_id"]:
        preset = dict(meta.model_dump(mode="json", exclude_none=True), client_id=auth["client_id"])
        if auth["client_secret"]:
            preset["client_secret"] = auth["client_secret"]
    storage = _FileTokenStorage(name, spec["url"], preset)
    box: dict[str, _OAuthCallback] = {}

    async def redirect_handler(url: str) -> None:
        import webbrowser

        cb = _OAuthCallback(auth["redirect_port"])
        cb.start()
        box["cb"] = cb
        _OAUTH_URLS[name] = url
        log.warning("connector %s: sign in in your browser: %s", name, url)
        with contextlib.suppress(Exception):
            webbrowser.open(url)

    async def callback_handler() -> tuple[str, str | None]:
        cb = box.pop("cb", None)
        if cb is None:
            raise ConnectorError("OAuth callback was not started")
        return await cb.wait()

    return OAuthClientProvider(server_url=spec["url"], client_metadata=meta, storage=storage,
                               redirect_handler=redirect_handler, callback_handler=callback_handler)


def _http_client_factory(url: str) -> Callable[..., Any]:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    loopback = host in ("127.0.0.1", "localhost", "::1")

    def factory(headers: dict | None = None, timeout: Any = None, auth: Any = None) -> Any:
        import httpx

        return httpx.AsyncClient(headers=headers, timeout=timeout or httpx.Timeout(30.0, read=300.0), auth=auth,
                                 follow_redirects=True, trust_env=not loopback)

    return factory


def _first_error(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:  # noqa: F821 (3.11+)
        e = e.exceptions[0]
    return e


try:  # Python 3.10 has no built-in ExceptionGroup
    BaseExceptionGroup  # noqa: B018
except NameError:  # pragma: no cover
    class BaseExceptionGroup(BaseException):  # type: ignore[no-redef]
        exceptions: tuple = ()


class Connector:
    """One MCP server from capy.yaml. Connects lazily; the session lives in its own task so that
    the transport's cancel scopes are entered and exited in the same task."""

    def __init__(self, name: str, spec: dict):
        self.name, self.spec = name, spec
        self.session: Any = None
        self.tools: list[Any] = []
        self.status = "not connected"
        self.error: str | None = None
        self.pids: set[int] = set()
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._ready: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def ensure(self) -> None:
        if self.spec.get("problem"):
            self.status, self.error = "failed", self.spec["problem"]
            raise ConnectorError(f"connector '{self.name}' is not available: {self.spec['problem']}")
        async with self._lock:
            if self.connected:
                return
            await self._shutdown()
            self._stop, self._ready, self.error = asyncio.Event(), asyncio.Event(), None
            self.status = "connecting"
            before = _child_pids() if self.spec["type"] == "stdio" else set()
            self._task = asyncio.ensure_future(self._run())
            try:
                await asyncio.wait_for(asyncio.shield(self._ready.wait()), timeout=max(30.0, self.spec["timeout"]))
            except asyncio.TimeoutError:
                self.error = self.error or "timed out while connecting"
            if self.spec["type"] == "stdio":
                browsers = {b.proc.pid for b in list(_LIVE) if b.proc is not None}
                self.pids = (_child_pids() - before) - browsers
                _CONNECTOR_PIDS.update(self.pids)
            if not self.connected:
                err = self.error or "connection closed"
                await self._shutdown()
                self.status, self.error = "failed", err
                raise ConnectorError(f"connector '{self.name}' is not available: {mask_secrets(err)}")

    async def _open(self, stack: contextlib.AsyncExitStack) -> tuple[Any, Any]:
        spec = self.spec
        if spec["type"] == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = dict(os.environ)
            env.update(spec["env"])
            env.setdefault("PYTHONIOENCODING", "utf-8")
            params = StdioServerParameters(command=spec["command"], args=spec["args"], env=env, cwd=spec["cwd"])
            read, write = await stack.enter_async_context(stdio_client(params))
            return read, write
        headers = dict(spec["headers"])
        auth = None
        if spec["auth"] and spec["auth"]["type"] == "bearer":
            headers["Authorization"] = "Bearer " + spec["auth"]["token"]
        elif spec["auth"] and spec["auth"]["type"] == "oauth":
            auth = _oauth_provider(self.name, spec)
        factory = _http_client_factory(spec["url"])
        if spec["type"] == "sse":
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(spec["url"], headers=headers, timeout=30, httpx_client_factory=factory, auth=auth))
            return read, write
        from mcp.client.streamable_http import streamablehttp_client

        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(spec["url"], headers=headers, timeout=30, httpx_client_factory=factory, auth=auth))
        return read, write

    async def _run(self) -> None:
        from mcp import ClientSession

        assert self._stop is not None and self._ready is not None
        try:
            async with contextlib.AsyncExitStack() as stack:
                read, write = await self._open(stack)
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                tools: list[Any] = []
                cursor = None
                for _ in range(20):
                    page = await session.list_tools(cursor) if cursor else await session.list_tools()
                    tools.extend(page.tools)
                    cursor = getattr(page, "nextCursor", None)
                    if not cursor:
                        break
                self.tools, self.session, self.status = tools, session, "connected"
                self._ready.set()
                await self._stop.wait()
        except BaseException as e:  # noqa: BLE001 - report every transport failure, including cancellation
            err = _first_error(e)
            if not isinstance(err, asyncio.CancelledError):
                self.error = f"{type(err).__name__}: {err}"[:500]
                log.warning("connector %s: %s", self.name, mask_secrets(self.error))
        finally:
            self.session = None
            if self.status == "connected":
                self.status = "disconnected"
            self._ready.set()

    async def _shutdown(self) -> None:
        task, stop = self._task, self._stop
        self._task = None
        if task is not None and not task.done():
            if stop is not None:
                stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(task, timeout=3)
        for pid in list(self.pids):
            _kill_tree(pid)
            _CONNECTOR_PIDS.discard(pid)
        self.pids = set()
        self.session = None

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown()
            self.status = "not connected"

    async def call(self, tool: str, arguments: dict) -> Any:
        from datetime import timedelta

        if not self.connected:
            raise ConnectorError(f"connector '{self.name}' is not connected")
        return await self.session.call_tool(tool, arguments, read_timeout_seconds=timedelta(seconds=self.spec["timeout"]))


_RETRYABLE = ("ClosedResourceError", "BrokenResourceError", "EndOfStream", "BrokenPipeError", "ConnectionError",
              "ConnectionResetError", "RemoteProtocolError", "ReadError", "ConnectError", "WriteError")


def _is_disconnect(e: BaseException) -> bool:
    e = _first_error(e)
    if type(e).__name__ in _RETRYABLE or isinstance(e, (ConnectionError, BrokenPipeError, EOFError)):
        return True
    text = str(e).lower()
    return "connection closed" in text or "closed resource" in text or "broken pipe" in text


class Router:
    """Holds the connectors from capy.yaml and implements list_connectors / find_tools / call_tool."""

    def __init__(self) -> None:
        self.connectors: dict[str, Connector] = {}
        self.warnings: list[str] = []

    async def update(self, servers: dict) -> None:
        for name in list(self.connectors):
            if name not in servers or servers[name] != self.connectors[name].spec:
                await self.connectors.pop(name).close()
        for name, spec in servers.items():
            if name not in self.connectors:
                self.connectors[name] = Connector(name, spec)

    async def close_all(self) -> None:
        for c in list(self.connectors.values()):
            with contextlib.suppress(Exception):
                await c.close()

    def _enabled(self) -> list[Connector]:
        return [c for c in self.connectors.values() if c.spec["enabled"]]

    def list_text(self) -> str:
        lines = []
        if not self.connectors:
            lines.append("No connectors configured. Add MCP servers under mcp_servers: in capy.yaml "
                         "(create one with: capybrowser init).")
        for c in self.connectors.values():
            if not c.spec["enabled"]:
                state = "disabled"
            elif c.status == "connected":
                state = f"connected, {len(c.tools)} tools"
            elif c.status == "failed":
                state = f"failed: {_cut(c.error or '', 160)}"
            else:
                state = "ready (connects on first use)"
            desc = f" — {c.spec['description']}" if c.spec["description"] else ""
            lines.append(f"- {c.name} [{c.spec['type']}] {state}{desc}")
        for w in self.warnings:
            lines.append(f"! {w}")
        if self.connectors:
            lines.append('Next: find_tools("what you need") to get tool names, then call_tool(tool, arguments).')
        return "\n".join(lines)

    async def _connect_all(self, only: str | None = None) -> list[str]:
        problems = []
        targets = [c for c in self._enabled() if only in (None, c.name)]
        results = await asyncio.gather(*(c.ensure() for c in targets), return_exceptions=True)
        for c, r in zip(targets, results, strict=True):
            if isinstance(r, BaseException):
                problems.append(f"{c.name}: {_cut(str(r), 160)}")
        return problems

    async def find(self, query: str, connector: str | None = None, limit: int = 8) -> str:
        if connector and connector not in self.connectors:
            return f"Error: unknown connector '{connector}'. Known: {', '.join(self.connectors) or 'none'}"
        if not self.connectors:
            return self.list_text()
        problems = await self._connect_all(connector)
        qwords = _words(query)
        expanded: set[str] = set()
        for w in qwords:
            expanded.add(w)
            expanded |= _SYN_INDEX.get(w, set())
            for key, group in _SYN_INDEX.items():
                if len(w) >= 4 and len(key) >= 4 and _stem(w) == _stem(key):
                    expanded |= group
        stems = {_stem(w) for w in expanded if len(w) >= 3}
        scored = []
        for c in self._enabled():
            if connector and c.name != connector:
                continue
            cwords = set(_words(c.name + " " + c.spec["description"]))
            for t in c.tools:
                name_words = set(_words(t.name))
                desc_words = set(_words(t.description or ""))
                score = 0.0
                for w in expanded:
                    if w in name_words:
                        score += 3
                    if w in desc_words:
                        score += 1
                    if w in cwords:
                        score += 2
                score += 0.5 * len(stems & ({_stem(x) for x in name_words | desc_words | cwords if len(x) >= 3}))
                if not qwords:
                    score = 1
                if score > 0:
                    scored.append((score, c.name, t))
        scored.sort(key=lambda x: (-x[0], x[1], x[2].name))
        limit = max(1, min(int(limit or 8), 25))
        lines = [_signature(f"{c}.{t.name}", t) for _, c, t in scored[:limit]]
        out = []
        if lines:
            out.append(f"Tools for '{_cut(query, 60)}' (arg? = optional). Call with call_tool(tool, arguments):")
            out += lines
            if len(scored) > limit:
                out.append(f"(+{len(scored) - limit} more; refine the query)")
        else:
            names = ", ".join(f"{c.name}({len(c.tools)})" for c in self._enabled() if c.connected)
            out.append(f"No tools match '{_cut(query, 60)}'. Connected: {names or 'none'}. "
                       "Try other words or find_tools('') to list everything.")
        if problems:
            out.append("Unavailable: " + "; ".join(problems))
        return "\n".join(out)

    def _resolve(self, tool: str) -> tuple[Connector, str] | str:
        tool = (tool or "").strip()
        for sep in (".", "__", "/", ":"):
            if sep in tool:
                cname, tname = tool.split(sep, 1)
                if cname in self.connectors:
                    return self.connectors[cname], tname
        hits = [(c, t.name) for c in self._enabled() for t in c.tools if t.name == tool]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return f"Error: '{tool}' exists in several connectors: " + ", ".join(f"{c.name}.{t}" for c, t in hits)
        return f"Error: unknown tool '{tool}'. Use find_tools to get the exact name (connector.tool)."

    async def call(self, tool: str, arguments: Any) -> str:
        args = parse_arguments(arguments)
        if isinstance(args, str):
            return args
        if not self.connectors:
            return self.list_text()
        target = self._resolve(tool)
        if isinstance(target, str) and "." not in tool and "__" not in tool:
            await self._connect_all()  # bare tool name: tool lists are needed to find it
            target = self._resolve(tool)
        if isinstance(target, str):
            return target
        conn, tname = target
        if not conn.spec["enabled"]:
            return f"Error: connector '{conn.name}' is disabled in capy.yaml"
        for attempt in range(2):
            try:
                await conn.ensure()
                if conn.tools and tname not in {t.name for t in conn.tools}:
                    return (f"Error: connector '{conn.name}' has no tool '{tname}'. "
                            f"Available: {', '.join(t.name for t in conn.tools[:30])}")
                res = await conn.call(tname, args)
                return format_tool_result(res)
            except ConnectorError as e:
                return f"Error: {e}"
            except BaseException as e:  # noqa: BLE001
                err = _first_error(e)
                if isinstance(err, asyncio.CancelledError):
                    raise
                if attempt == 0 and _is_disconnect(err):
                    log.warning("connector %s dropped (%s); reconnecting", conn.name, type(err).__name__)
                    await conn.close()
                    continue
                return f"Error: {conn.name}.{tname} failed: {type(err).__name__}: {_cut(str(err), 400)}"
        return f"Error: connector '{conn.name}' disconnected again; try later"


def parse_arguments(arguments: Any) -> dict | str:
    """Accept a dict, a JSON string (even double-encoded) or nothing. Returns a dict or an error string."""
    value = arguments
    for _ in range(3):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
            try:
                value = json.loads(text)
                continue
            except ValueError:
                try:
                    import ast

                    value = ast.literal_eval(text)
                    continue
                except Exception:
                    return 'Error: arguments must be a JSON object, e.g. {"query": "text"}'
        break
    return 'Error: arguments must be a JSON object, e.g. {"query": "text"}'


def format_tool_result(res: Any) -> str:
    parts = []
    for c in getattr(res, "content", None) or []:
        kind = getattr(c, "type", "")
        if kind == "text":
            parts.append(getattr(c, "text", ""))
        elif kind == "resource":
            r = getattr(c, "resource", None)
            parts.append(getattr(r, "text", None) or f"[resource {getattr(r, 'uri', '')}]")
        elif kind:
            parts.append(f"[{kind} omitted]")
    text = "\n".join(p for p in parts if p)
    if not text and getattr(res, "structuredContent", None) is not None:
        text = json.dumps(res.structuredContent, ensure_ascii=False)
    text = text or "(empty result)"
    return ("Error from tool: " + text) if getattr(res, "isError", False) else text


# --------------------------------------------------------------------------- config state + hot reload

class ConfigState:
    def __init__(self, explicit: str | None, cli: dict):
        self.explicit = explicit
        self.cli = {k: v for k, v in cli.items() if v is not None}
        self.key: tuple = ()
        self.current: dict = {"path": None, "browser": {}, "mcp_servers": {}, "warnings": [], "files": []}
        self.error: str | None = None
        self.lock: asyncio.Lock | None = None

    def effective(self) -> dict:
        b = dict(self.current["browser"])
        merged = {"headed": False, "browser": None, "lang": None, "max_chars": MAX_CHARS, "profile_dir": None,
                  "allow_local_files": False, "nav_timeout": float(os.environ.get("BROWSER_MCP_NAV_TIMEOUT", "25"))}
        merged.update({k: v for k, v in b.items() if v is not None})
        merged.update(self.cli)
        return merged

    def load_initial(self) -> dict:
        cfg = load_config(self.explicit)
        self.current = cfg
        self.key = _config_key(cfg["path"], cfg["files"])
        return cfg

    def _path(self) -> str | None:
        if self.explicit and not os.path.isfile(self.explicit):
            return self.current.get("path")  # an explicit file that vanished: keep the last good config
        try:
            return find_config(self.explicit)
        except FileNotFoundError:
            return None

    def changed(self) -> bool:
        if self.explicit and not os.path.isfile(self.explicit):
            return False  # keep the last good config until the file is back
        return _config_key(self._path(), self.current.get("files")) != self.key

    async def maybe_reload(self) -> None:
        if not self.changed():
            return
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            if not self.changed():
                return
            path = self._path()
            if path and not os.path.isfile(path):
                return
            new_key = _config_key(path, self.current.get("files"))
            try:
                cfg = load_config(path) if path else {"path": None, "browser": {}, "mcp_servers": {},
                                                      "warnings": [], "files": []}
            except (ConfigError, OSError) as e:
                self.key = new_key
                self.error = f"capy.yaml was not reloaded: {e}"
                ROUTER.warnings = [self.error] + self.current.get("warnings", [])
                log.warning("%s", self.error)
                return
            self.key, self.current, self.error = _config_key(path, cfg["files"]), cfg, None
            await apply_config(self)
            log.info("capy.yaml reloaded (%s)", path or "no file")


CONFIG_STATE: ConfigState | None = None
ROUTER = Router()


async def apply_config(state: ConfigState) -> None:
    """Apply browser settings and connectors. Launch settings take effect on the next browser start."""
    global NAV_TIMEOUT, _browser
    eff = state.effective()
    old = (_CONFIG.get("headed"), _CONFIG.get("path"), _CONFIG.get("lang"), _CONFIG.get("profile_dir"))
    _CONFIG.update(headed=bool(eff["headed"]), path=eff["browser"], lang=eff["lang"], max_chars=int(eff["max_chars"]),
                   profile_dir=eff["profile_dir"], allow_files=bool(eff["allow_local_files"]) or _env_allows_files())
    NAV_TIMEOUT = float(eff["nav_timeout"])
    new = (_CONFIG["headed"], _CONFIG["path"], _CONFIG["lang"], _CONFIG["profile_dir"])
    if _browser is not None:
        _browser.max_chars = _CONFIG["max_chars"]
        if new != old:
            b, _browser = _browser, None
            await b.close()
    ROUTER.warnings = list(state.current.get("warnings", []))
    await ROUTER.update(state.current["mcp_servers"])


def _env_allows_files() -> bool:
    return os.environ.get("BROWSER_MCP_ALLOW_FILE", "").strip().lower() in ("1", "true", "yes", "on")


async def _reload_if_needed() -> None:
    if CONFIG_STATE is not None:
        with contextlib.suppress(Exception):
            await CONFIG_STATE.maybe_reload()


def _limit(text: str) -> str:
    return clip(mask_secrets(text), int(_CONFIG.get("max_chars") or MAX_CHARS))


async def tool_list_connectors() -> str:
    await _reload_if_needed()
    return _limit(ROUTER.list_text())


async def tool_find_tools(query: str = "", connector: str | None = None, limit: int = 8) -> str:
    await _reload_if_needed()
    try:
        return _limit(await ROUTER.find(str(query or ""), connector or None, limit))
    except Exception as e:  # never raise into the MCP layer
        return _limit(f"Error: find_tools failed: {type(e).__name__}: {e}")


async def tool_call_tool(tool: str, arguments: Any = None) -> str:
    await _reload_if_needed()
    try:
        return _limit(await ROUTER.call(str(tool or ""), arguments))
    except Exception as e:
        return _limit(f"Error: call_tool failed: {type(e).__name__}: {e}")


def init_cli(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="capybrowser init", description="Create a capy.yaml template")
    ap.add_argument("path", nargs="?", default="capy.yaml")
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = ap.parse_args(argv)
    path = os.path.expanduser(args.path)
    if os.path.exists(path) and not args.force:
        sys.exit(f"{path} already exists (use --force to overwrite)")
    with open(path, "w", encoding="utf-8") as f:
        f.write(CAPY_YAML_TEMPLATE)
    print(f"Created {os.path.abspath(path)}")


def check_cli(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="capybrowser check", description="Validate capy.yaml (and optionally connect)")
    ap.add_argument("-c", "--config", default=None)
    ap.add_argument("--connect", action="store_true", help="also connect to every server and count its tools")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as e:
        sys.exit(f"config error: {mask_secrets(e)}")
    print(f"config: {cfg['path'] or '(none - built-in browser only)'}")
    opts = ", ".join(f"{k}={v}" for k, v in cfg["browser"].items() if v is not None)
    print("browser: " + (opts or "defaults"))
    for w in cfg["warnings"]:
        print(f"warning: {w}")

    async def run() -> None:
        await ROUTER.update(cfg["mcp_servers"])
        if args.connect:
            await ROUTER._connect_all()
        print(mask_secrets(ROUTER.list_text()))
        await ROUTER.close_all()

    asyncio.run(run())
    kill_connector_processes()


# --------------------------------------------------------------------------- capy.yaml editing (keeps comments)

def _yaml_scalar(value: Any) -> str:
    import yaml

    return yaml.safe_dump([value], default_flow_style=True, allow_unicode=True, width=10**6).strip()[1:-1]


def _yaml_block(name: str, spec: dict, indent: str) -> list[str]:
    import yaml

    text = yaml.safe_dump({name: spec}, sort_keys=False, allow_unicode=True, default_flow_style=None, width=10**6, indent=2)
    return [indent + line for line in text.rstrip("\n").split("\n")]


_TOP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*)\s*:(.*)$")


def _sections(lines: list[str]) -> dict[str, tuple[int, int]]:
    starts = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := _TOP_RE.match(ln))]
    out = {}
    for k, (i, name) in enumerate(starts):
        out[name] = (i, starts[k + 1][0] if k + 1 < len(starts) else len(lines))
    return out


def _is_content(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _section_indent(lines: list[str], start: int, end: int) -> str:
    for ln in lines[start + 1:end]:
        if _is_content(ln):
            return ln[: len(ln) - len(ln.lstrip())] or "  "
    return "  "


def _entries(lines: list[str], start: int, end: int, indent: str) -> dict[str, tuple[int, int]]:
    pat = re.compile(r"^" + re.escape(indent) + r"([^\s#][^:]*?)\s*:(.*)$")
    heads = [(i, m.group(1).strip().strip("'\"")) for i in range(start + 1, end) if (m := pat.match(lines[i]))]
    last_content = max([i for i in range(start + 1, end) if _is_content(lines[i])] or [start])
    out = {}
    for k, (i, name) in enumerate(heads):
        stop = heads[k + 1][0] if k + 1 < len(heads) else last_content + 1
        while stop - 1 > i and not _is_content(lines[stop - 1]):
            stop -= 1
        out[name] = (i, stop)
    return out


class CapyFile:
    """Edits capy.yaml as text so comments and formatting outside the changed lines are kept.
    Every change is validated with the same rules as the server before it is written."""

    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))

    def read(self) -> str:
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def raw(self, text: str | None = None) -> dict:
        import yaml

        try:
            data = yaml.safe_load(self.read() if text is None else text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def validate(self, text: str) -> dict:
        return parse_config_text(text, self.path)

    def write(self, text: str) -> dict:
        cfg = self.validate(text)  # raises ConfigError, nothing is written
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        os.replace(tmp, self.path)
        return cfg

    # -- connectors
    def _servers_section(self, lines: list[str]) -> tuple[int, int, str]:
        secs = _sections(lines)
        if "mcp_servers" not in secs:
            lines.extend(["", "mcp_servers:"])
            secs = _sections(lines)
        start, end = secs["mcp_servers"]
        rest = lines[start].split(":", 1)[1].split("#", 1)[0].strip()
        if rest and rest != "{}":
            raise ConfigError("mcp_servers is written in one line (flow style); edit it in the Config tab")
        if rest == "{}":
            lines[start] = re.sub(r":\s*\{\}", ":", lines[start], count=1)
        return start, end, _section_indent(lines, start, end)

    def set_server(self, name: str, spec: dict, must_exist: bool | None = None) -> dict:
        text = self.read()
        lines = text.split("\n") if text else []
        start, end, ind = self._servers_section(lines)
        entries = _entries(lines, start, end, ind)
        if must_exist is True and name not in entries:
            raise ConfigError(f"connector '{name}' does not exist")
        if must_exist is False and name in entries:
            raise ConfigError(f"connector '{name}' already exists - choose another name")
        block = _yaml_block(name, spec, ind)
        if name in entries:
            a, b = entries[name]
            lines[a:b] = block
        else:
            pos = (max(entries.values(), key=lambda r: r[1])[1] if entries else start + 1)
            lines[pos:pos] = block
        return self.write("\n".join(lines))

    def delete_server(self, name: str) -> dict:
        lines = self.read().split("\n")
        start, end, ind = self._servers_section(lines)
        entries = _entries(lines, start, end, ind)
        if name not in entries:
            raise ConfigError(f"connector '{name}' does not exist")
        a, b = entries[name]
        del lines[a:b]
        start, end = _sections(lines)["mcp_servers"]
        if not _entries(lines, start, end, ind):
            lines[start] = re.sub(r"^mcp_servers\s*:\s*(#.*)?$", lambda m: "mcp_servers: {}" + (" " + m.group(1) if m.group(1) else ""),
                                  lines[start])
        return self.write("\n".join(lines))

    def set_enabled(self, name: str, enabled: bool) -> dict:
        lines = self.read().split("\n")
        start, end, ind = self._servers_section(lines)
        entries = _entries(lines, start, end, ind)
        if name not in entries:
            raise ConfigError(f"connector '{name}' does not exist")
        a, b = entries[name]
        if lines[a].split(":", 1)[1].split("#", 1)[0].strip():  # one-line entry: rewrite it
            spec = dict(self.raw()["mcp_servers"][name])
            spec["enabled"] = enabled
            return self.set_server(name, spec, must_exist=True)
        child = None
        for i in range(a + 1, b):
            if _is_content(lines[i]):
                child = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                break
        child = child or ind * 2
        for i in range(a + 1, b):
            if re.match(r"^" + re.escape(child) + r"enabled\s*:", lines[i]):
                lines[i] = re.sub(r"(enabled\s*:\s*)[^#\s]*", lambda m: m.group(1) + ("true" if enabled else "false"), lines[i], count=1)
                break
        else:
            lines.insert(a + 1, f"{child}enabled: {'true' if enabled else 'false'}")
        return self.write("\n".join(lines))

    # -- browser section
    def set_browser(self, values: dict) -> dict:
        lines = self.read().split("\n") if self.read() else []
        secs = _sections(lines)
        if "browser" not in secs:
            insert_at = secs["mcp_servers"][0] if "mcp_servers" in secs else len(lines)
            lines[insert_at:insert_at] = ["browser:", ""]
            secs = _sections(lines)
        start, end = secs["browser"]
        if lines[start].split(":", 1)[1].split("#", 1)[0].strip() in ("{}",):
            lines[start] = re.sub(r":\s*\{\}", ":", lines[start], count=1)
        elif lines[start].split(":", 1)[1].split("#", 1)[0].strip():
            raise ConfigError("browser is written in one line (flow style); edit it in the Config tab")
        ind = _section_indent(lines, start, end)
        for key, value in values.items():
            if key not in _BROWSER_KEYS:
                raise ConfigError(f"browser: unknown option {key}")
            start, end = _sections(lines)["browser"]
            live = re.compile(r"^" + re.escape(ind) + re.escape(key) + r"\s*:")
            dead = re.compile(r"^" + re.escape(ind) + r"#\s*" + re.escape(key) + r"\s*:")
            idx = next((i for i in range(start + 1, end) if live.match(lines[i])), None)
            if idx is None:
                idx = next((i for i in range(start + 1, end) if dead.match(lines[i])), None)
                if idx is not None and value is not None:
                    comment = lines[idx].split("#", 2)[2] if lines[idx].count("#") >= 2 else ""
                    lines[idx] = f"{ind}{key}: {_yaml_scalar(value)}" + (f"   #{comment}" if comment else "")
                    continue
            if value is None:
                if idx is not None and live.match(lines[idx]):
                    del lines[idx]
                continue
            if idx is not None and live.match(lines[idx]):
                m = re.match(r"^(\s*" + re.escape(key) + r"\s*:\s*)([^#]*?)(\s+#.*)?$", lines[idx])
                lines[idx] = (m.group(1) if m else f"{ind}{key}: ") + _yaml_scalar(value) + ((m.group(3) or "") if m else "")
            else:
                last = max([i for i in range(start, end) if _is_content(lines[i])] or [start])
                lines.insert(last + 1, f"{ind}{key}: {_yaml_scalar(value)}")
        return self.write("\n".join(lines))


# --------------------------------------------------------------------------- .env manager

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_path_for(config_path: str, raw: dict | None = None) -> str:
    name = (raw or {}).get("env_file") if isinstance((raw or {}).get("env_file"), str) else None
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), os.path.expanduser(name or ".env"))


def referenced_vars(text: str) -> list[str]:
    seen: list[str] = []
    for ln in text.split("\n"):
        if ln.lstrip().startswith("#"):
            continue
        for v in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", ln):
            if v not in seen:
                seen.append(v)
    return seen


def env_status(config_path: str, text: str, raw: dict) -> list[dict]:
    path = env_path_for(config_path, raw)
    file_vals = load_dotenv(path) if os.path.isfile(path) else {}
    names = referenced_vars(text) + [k for k in file_vals if k not in referenced_vars(text)]
    out = []
    for n in names:
        if os.environ.get(n):
            src = "environment"
        elif file_vals.get(n):
            src = ".env"
        else:
            src = None
        out.append({"name": n, "set": src is not None, "source": src, "in_env_file": n in file_vals,
                    "used": n in referenced_vars(text)})
    return out


def _ensure_gitignore(folder: str) -> bool:
    gi = os.path.join(folder, ".gitignore")
    probe = folder
    in_git = False
    for _ in range(40):
        if os.path.exists(os.path.join(probe, ".git")):
            in_git = True
            break
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not (in_git or os.path.isfile(gi)):
        return False
    existing = open(gi, encoding="utf-8").read() if os.path.isfile(gi) else ""
    if any(ln.strip() in (".env", "/.env", "*.env", ".env*") for ln in existing.splitlines()):
        return False
    with open(gi, "a", encoding="utf-8") as f:
        f.write(("" if not existing or existing.endswith("\n") else "\n") + ".env\n")
    return True


def set_env_value(config_path: str, raw: dict, name: str, value: str | None) -> None:
    if not _ENV_NAME.match(name or ""):
        raise ConfigError("variable names may contain only letters, digits and '_' and must not start with a digit")
    if value is not None and ("\n" in value or "\r" in value):
        raise ConfigError("the value must be a single line")
    path = env_path_for(config_path, raw)
    lines = open(path, encoding="utf-8-sig").read().split("\n") if os.path.isfile(path) else []
    pat = re.compile(r"^\s*(export\s+)?" + re.escape(name) + r"\s*=")
    lines = [ln for ln in lines if not pat.match(ln)] if value is None else lines
    if value is not None:
        if re.fullmatch(r"[A-Za-z0-9_\-./:@+=,~]*", value):
            rendered = value
        elif '"' not in value:
            rendered = f'"{value}"'
        elif "'" not in value:
            rendered = f"'{value}'"
        else:
            raise ConfigError("the value cannot contain both ' and \" quotes")
        for i, ln in enumerate(lines):
            if pat.match(ln):
                lines[i] = f"{name}={rendered}"
                break
        else:
            while lines and lines[-1] == "":
                lines.pop()
            lines.append(f"{name}={rendered}")
        register_secret(value)
    text = "\n".join(lines).rstrip("\n") + "\n" if lines else ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(path + ".tmp", path)
    _ensure_gitignore(os.path.dirname(path))


# --------------------------------------------------------------------------- presets + chat configs

PRESETS: list[dict] = [
    {"id": "remote", "title": "Remote MCP (URL + OAuth / Bearer)", "kind": "http",
     "hint": "Any cloud MCP server: Gmail / Google Workspace, Zapier MCP, Composio, Cloudflare Workers MCP…",
     "fields": [{"key": "name", "label": "Name", "default": "remote"},
                {"key": "url", "label": "Server URL", "default": "https://", "placeholder": "https://example.com/mcp"},
                {"key": "transport", "label": "Transport", "choices": ["streamable-http", "sse"], "default": "streamable-http"},
                {"key": "auth", "label": "Authorization", "choices": ["none", "oauth", "bearer"], "default": "oauth"},
                {"key": "token_var", "label": "Token variable (bearer)", "default": "MCP_TOKEN"}]},
    {"id": "github", "title": "GitHub", "kind": "http", "hint": "Official GitHub MCP server (issues, PRs, code).",
     "fields": [{"key": "name", "label": "Name", "default": "github"},
                {"key": "auth", "label": "Authorization", "choices": ["bearer", "oauth"], "default": "bearer"},
                {"key": "token_var", "label": "Token variable", "default": "GITHUB_TOKEN"}]},
    {"id": "notion", "title": "Notion", "kind": "http", "hint": "Notion pages and databases (OAuth sign-in).",
     "fields": [{"key": "name", "label": "Name", "default": "notion"}]},
    {"id": "filesystem", "title": "Filesystem", "kind": "stdio", "hint": "Read and write files in one folder (needs Node.js).",
     "fields": [{"key": "name", "label": "Name", "default": "files"},
                {"key": "folder", "label": "Folder", "default": "", "placeholder": "/path/to/folder"}]},
    {"id": "postgres", "title": "PostgreSQL", "kind": "stdio", "hint": "Read-only SQL queries (needs Node.js).",
     "fields": [{"key": "name", "label": "Name", "default": "postgres"},
                {"key": "url_var", "label": "Connection URL variable", "default": "DATABASE_URL"}]},
    {"id": "sqlite", "title": "SQLite", "kind": "stdio", "hint": "Query a local SQLite database (uses uvx).",
     "fields": [{"key": "name", "label": "Name", "default": "sqlite"},
                {"key": "db", "label": "Database file", "default": "", "placeholder": "/path/to/data.sqlite"}]},
    {"id": "stdio", "title": "Custom local MCP (stdio)", "kind": "stdio", "hint": "Any command that speaks MCP over stdio.",
     "fields": [{"key": "name", "label": "Name", "default": "local"},
                {"key": "command", "label": "Command", "default": "", "placeholder": "uvx"},
                {"key": "args", "label": "Arguments (one per line)", "default": "", "multiline": True,
                 "placeholder": "mcp-server-time"},
                {"key": "description", "label": "Description (helps find_tools)", "default": ""}]},
    {"id": "http", "title": "Custom remote MCP (http / sse)", "kind": "http", "hint": "A remote server with optional headers.",
     "fields": [{"key": "name", "label": "Name", "default": "custom"},
                {"key": "url", "label": "Server URL", "default": "https://"},
                {"key": "transport", "label": "Transport", "choices": ["streamable-http", "sse"], "default": "streamable-http"},
                {"key": "auth", "label": "Authorization", "choices": ["none", "oauth", "bearer"], "default": "none"},
                {"key": "token_var", "label": "Token variable (bearer)", "default": "MCP_TOKEN"},
                {"key": "description", "label": "Description (helps find_tools)", "default": ""}]},
]


def preset_spec(preset_id: str, f: dict) -> tuple[str, dict]:
    def val(key: str, default: str = "") -> str:
        v = f.get(key, default)
        return str(v).strip() if v is not None else default

    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if preset is None:
        raise ConfigError(f"unknown preset {preset_id}")
    defaults = {fd["key"]: fd.get("default", "") for fd in preset["fields"]}
    f = {**defaults, **{k: v for k, v in f.items() if v not in (None, "")}}
    name = val("name") or preset_id
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$", name):
        raise ConfigError("name may contain only letters, digits, '-' and '_'")
    var = val("token_var") or "MCP_TOKEN"
    if not _ENV_NAME.match(var):
        raise ConfigError("the token variable must look like MY_TOKEN")

    def auth(kind: str) -> dict | None:
        if kind == "bearer":
            return {"type": "bearer", "token": "${" + var + "}"}
        if kind == "oauth":
            return {"type": "oauth"}
        return None

    if preset_id in ("remote", "http"):
        spec: dict[str, Any] = {"type": val("transport", "streamable-http"), "url": val("url")}
        if val("description"):
            spec["description"] = val("description")
        a = auth(val("auth", "none"))
        if a:
            spec["auth"] = a
    elif preset_id == "github":
        spec = {"type": "streamable-http", "url": "https://api.githubcopilot.com/mcp/", "description": "GitHub: code, issues, pull requests",
                "auth": auth(val("auth", "bearer")) or {"type": "bearer", "token": "${" + var + "}"}}
    elif preset_id == "notion":
        spec = {"type": "streamable-http", "url": "https://mcp.notion.com/mcp", "description": "Notion: pages, notes, tasks, databases",
                "auth": {"type": "oauth"}}
    elif preset_id == "filesystem":
        folder = val("folder")
        if not folder:
            raise ConfigError("choose a folder")
        spec = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", folder],
                "description": "files and folders"}
    elif preset_id == "postgres":
        uv = val("url_var") or "DATABASE_URL"
        if not _ENV_NAME.match(uv):
            raise ConfigError("the connection URL variable must look like DATABASE_URL")
        spec = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "${" + uv + "}"],
                "description": "PostgreSQL database (SQL)"}
    elif preset_id == "sqlite":
        db = val("db")
        if not db:
            raise ConfigError("choose a database file")
        spec = {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", db], "description": "SQLite database (SQL)"}
    elif preset_id == "stdio":
        if not val("command"):
            raise ConfigError("command is required")
        args = f.get("args") or []
        if isinstance(args, str):
            args = [a.strip() for a in args.split("\n") if a.strip()]
        spec = {"command": val("command"), "args": [str(a) for a in args]}
        if val("description"):
            spec["description"] = val("description")
    else:
        raise ConfigError(f"unknown preset {preset_id}")
    return name, spec


def launch_command(config_path: str) -> tuple[str, list[str]]:
    """Absolute command line an AI app should use to start CapyBrowser with this config."""
    cfg = os.path.abspath(config_path)
    exe = shutil.which("capybrowser")
    if exe:
        return exe, ["--config", cfg]
    uv = shutil.which("uv") or "uv"
    return uv, ["run", os.path.abspath(__file__), "--config", cfg]


def chat_configs(config_path: str) -> list[dict]:
    cmd, args = launch_command(config_path)
    entry = {"command": cmd, "args": args}
    mcp_json = json.dumps({"mcpServers": {"capybrowser": entry}}, indent=2, ensure_ascii=False)
    toml_args = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
    toml = f'[mcp_servers.capybrowser]\ncommand = {json.dumps(cmd, ensure_ascii=False)}\nargs = [{toml_args}]\n'
    shell_cmd = " ".join(_shell_quote(x) for x in [cmd, *args])
    if IS_WIN:
        claude_path = r"%APPDATA%\Claude\claude_desktop_config.json"
        agent = f"$env:CAPYBROWSER_CMD = '{shell_cmd}'\nuv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/examples/ollama_agent.py \"your task\""
    else:
        claude_path = ("~/Library/Application Support/Claude/claude_desktop_config.json" if sys.platform == "darwin"
                       else "~/.config/Claude/claude_desktop_config.json")
        agent = (f"CAPYBROWSER_CMD={_shell_quote(shell_cmd)} \\\n  uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/"
                 "main/examples/ollama_agent.py \"your task\"")
    openwebui = f"uvx mcpo --port 8000 -- {shell_cmd}\n# then in Open WebUI: Settings > Tools > add http://localhost:8000"
    return [
        {"id": "lmstudio", "title": "LM Studio", "where": "Program > Install > Edit mcp.json", "lang": "json", "text": mcp_json},
        {"id": "claude", "title": "Claude Desktop", "where": claude_path, "lang": "json", "text": mcp_json},
        {"id": "codex", "title": "Codex CLI", "where": "~/.codex/config.toml", "lang": "toml", "text": toml},
        {"id": "cursor", "title": "Cursor", "where": ".cursor/mcp.json or ~/.cursor/mcp.json", "lang": "json", "text": mcp_json},
        {"id": "openwebui", "title": "Open WebUI (via mcpo)", "where": "terminal", "lang": "bash", "text": openwebui},
        {"id": "ollama", "title": "Python Ollama agent", "where": "terminal", "lang": "bash", "text": agent},
    ]


def _shell_quote(s: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_\-./:=@%+,\\]+", s):
        return s
    if IS_WIN:
        return '"' + s.replace('"', '\\"') + '"'
    return "'" + s.replace("'", "'\"'\"'") + "'"


def estimate_tokens(text: str) -> int:
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return int(ascii_chars / 4 + (len(text) - ascii_chars) / 2.2) + 1

UI_LOGO = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" shape-rendering="crispEdges"><rect x="10" y="6" width="2" height="1" fill="#5cb85c"/><rect x="9" y="7" width="2" height="1" fill="#5cb85c"/><rect x="11" y="7" width="1" height="1" fill="#2a1810"/><rect x="8" y="8" width="6" height="1" fill="#2a1810"/><rect x="7" y="9" width="1" height="1" fill="#2a1810"/><rect x="8" y="9" width="1" height="1" fill="#f2b84b"/><rect x="9" y="9" width="4" height="1" fill="#d9824f"/><rect x="13" y="9" width="1" height="1" fill="#f2b84b"/><rect x="14" y="9" width="1" height="1" fill="#2a1810"/><rect x="6" y="10" width="1" height="1" fill="#2a1810"/><rect x="7" y="10" width="2" height="1" fill="#d9824f"/><rect x="9" y="10" width="1" height="1" fill="#f7d9bf"/><rect x="10" y="10" width="5" height="1" fill="#d9824f"/><rect x="15" y="10" width="1" height="1" fill="#2a1810"/><rect x="6" y="11" width="1" height="1" fill="#2a1810"/><rect x="7" y="11" width="1" height="1" fill="#d9824f"/><rect x="8" y="11" width="1" height="1" fill="#f7d9bf"/><rect x="9" y="11" width="6" height="1" fill="#d9824f"/><rect x="15" y="11" width="1" height="1" fill="#2a1810"/><rect x="6" y="12" width="1" height="1" fill="#2a1810"/><rect x="7" y="12" width="8" height="1" fill="#d9824f"/><rect x="15" y="12" width="4" height="1" fill="#2a1810"/><rect x="20" y="12" width="3" height="1" fill="#2a1810"/><rect x="6" y="13" width="1" height="1" fill="#2a1810"/><rect x="7" y="13" width="7" height="1" fill="#d9824f"/><rect x="14" y="13" width="1" height="1" fill="#f2b84b"/><rect x="15" y="13" width="1" height="1" fill="#2a1810"/><rect x="16" y="13" width="1" height="1" fill="#c08a5e"/><rect x="17" y="13" width="1" height="1" fill="#9a6440"/><rect x="18" y="13" width="2" height="1" fill="#2a1810"/><rect x="20" y="13" width="1" height="1" fill="#c08a5e"/><rect x="21" y="13" width="1" height="1" fill="#9a6440"/><rect x="22" y="13" width="1" height="1" fill="#2a1810"/><rect x="7" y="14" width="1" height="1" fill="#2a1810"/><rect x="8" y="14" width="5" height="1" fill="#d9824f"/><rect x="13" y="14" width="1" height="1" fill="#f2b84b"/><rect x="14" y="14" width="2" height="1" fill="#2a1810"/><rect x="16" y="14" width="7" height="1" fill="#9a6440"/><rect x="23" y="14" width="2" height="1" fill="#2a1810"/><rect x="6" y="15" width="8" height="1" fill="#2a1810"/><rect x="14" y="15" width="11" height="1" fill="#9a6440"/><rect x="25" y="15" width="1" height="1" fill="#2a1810"/><rect x="4" y="16" width="2" height="1" fill="#2a1810"/><rect x="6" y="16" width="20" height="1" fill="#9a6440"/><rect x="26" y="16" width="1" height="1" fill="#2a1810"/><rect x="3" y="17" width="1" height="1" fill="#2a1810"/><rect x="4" y="17" width="3" height="1" fill="#9a6440"/><rect x="7" y="17" width="3" height="1" fill="#c08a5e"/><rect x="10" y="17" width="10" height="1" fill="#9a6440"/><rect x="20" y="17" width="2" height="1" fill="#140b07"/><rect x="22" y="17" width="4" height="1" fill="#9a6440"/><rect x="26" y="17" width="1" height="1" fill="#2a1810"/><rect x="2" y="18" width="1" height="1" fill="#2a1810"/><rect x="3" y="18" width="3" height="1" fill="#9a6440"/><rect x="6" y="18" width="4" height="1" fill="#c08a5e"/><rect x="10" y="18" width="10" height="1" fill="#9a6440"/><rect x="20" y="18" width="1" height="1" fill="#140b07"/><rect x="21" y="18" width="1" height="1" fill="#ffffff"/><rect x="22" y="18" width="4" height="1" fill="#9a6440"/><rect x="26" y="18" width="1" height="1" fill="#2a1810"/><rect x="2" y="19" width="1" height="1" fill="#2a1810"/><rect x="3" y="19" width="23" height="1" fill="#9a6440"/><rect x="26" y="19" width="1" height="1" fill="#2a1810"/><rect x="1" y="20" width="1" height="1" fill="#2a1810"/><rect x="2" y="20" width="22" height="1" fill="#9a6440"/><rect x="24" y="20" width="2" height="1" fill="#6b4129"/><rect x="26" y="20" width="1" height="1" fill="#2a1810"/><rect x="1" y="21" width="1" height="1" fill="#2a1810"/><rect x="2" y="21" width="21" height="1" fill="#9a6440"/><rect x="23" y="21" width="3" height="1" fill="#6b4129"/><rect x="26" y="21" width="1" height="1" fill="#2a1810"/><rect x="1" y="22" width="1" height="1" fill="#2a1810"/><rect x="2" y="22" width="20" height="1" fill="#9a6440"/><rect x="22" y="22" width="2" height="1" fill="#6b4129"/><rect x="24" y="22" width="2" height="1" fill="#1d120c"/><rect x="26" y="22" width="1" height="1" fill="#2a1810"/><rect x="1" y="23" width="1" height="1" fill="#2a1810"/><rect x="2" y="23" width="20" height="1" fill="#9a6440"/><rect x="22" y="23" width="4" height="1" fill="#6b4129"/><rect x="26" y="23" width="1" height="1" fill="#2a1810"/><rect x="2" y="24" width="1" height="1" fill="#2a1810"/><rect x="3" y="24" width="19" height="1" fill="#9a6440"/><rect x="22" y="24" width="1" height="1" fill="#2a1810"/><rect x="23" y="24" width="2" height="1" fill="#6b4129"/><rect x="25" y="24" width="1" height="1" fill="#2a1810"/><rect x="3" y="25" width="1" height="1" fill="#2a1810"/><rect x="4" y="25" width="2" height="1" fill="#9a6440"/><rect x="6" y="25" width="3" height="1" fill="#2a1810"/><rect x="9" y="25" width="8" height="1" fill="#9a6440"/><rect x="17" y="25" width="2" height="1" fill="#2a1810"/><rect x="19" y="25" width="3" height="1" fill="#9a6440"/><rect x="22" y="25" width="3" height="1" fill="#2a1810"/><rect x="3" y="26" width="1" height="1" fill="#2a1810"/><rect x="4" y="26" width="1" height="1" fill="#9a6440"/><rect x="5" y="26" width="1" height="1" fill="#2a1810"/><rect x="8" y="26" width="1" height="1" fill="#2a1810"/><rect x="9" y="26" width="1" height="1" fill="#9a6440"/><rect x="10" y="26" width="1" height="1" fill="#2a1810"/><rect x="15" y="26" width="1" height="1" fill="#2a1810"/><rect x="16" y="26" width="1" height="1" fill="#9a6440"/><rect x="17" y="26" width="1" height="1" fill="#2a1810"/><rect x="19" y="26" width="1" height="1" fill="#2a1810"/><rect x="20" y="26" width="1" height="1" fill="#9a6440"/><rect x="21" y="26" width="1" height="1" fill="#2a1810"/><rect x="3" y="27" width="2" height="1" fill="#2a1810"/><rect x="9" y="27" width="2" height="1" fill="#2a1810"/><rect x="15" y="27" width="2" height="1" fill="#2a1810"/><rect x="20" y="27" width="2" height="1" fill="#2a1810"/></svg>'

UI_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CapyBrowser</title>
<style>
:root{--bg:#120d0a;--panel:#1c1511;--panel2:#241b16;--line:#3a2b22;--text:#f3e7dc;--muted:#b49b89;--dim:#8a7464;
--accent:#d9824f;--accent2:#f2b84b;--ok:#5cb85c;--warn:#f2b84b;--err:#e0574a;--info:#2f9e92;--radius:14px;
--mono:ui-monospace,"JetBrains Mono","Cascadia Code",Consolas,Menlo,monospace}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button,input,select,textarea{font:inherit;color:inherit}
header{display:flex;align-items:center;gap:14px;padding:14px 22px;border-bottom:1px solid var(--line);
background:linear-gradient(90deg,#1e140f,#2a1a12 60%,#1e140f);position:sticky;top:0;z-index:5}
header .logo{width:42px;height:42px;image-rendering:pixelated}
header h1{font-size:20px;margin:0;font-weight:800;letter-spacing:.2px}
header h1 span{color:var(--accent)}
header .meta{color:var(--dim);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:46vw}
header .spacer{flex:1}
.langs button{background:transparent;border:1px solid var(--line);padding:4px 10px;border-radius:999px;cursor:pointer;color:var(--muted)}
.langs button.on{background:var(--accent);border-color:var(--accent);color:#1b120d;font-weight:700}
nav{display:flex;gap:6px;padding:12px 22px 0;border-bottom:1px solid var(--line);overflow-x:auto}
nav button{background:none;border:none;padding:10px 14px;border-radius:10px 10px 0 0;cursor:pointer;color:var(--muted);
font-weight:600;white-space:nowrap;border-bottom:2px solid transparent}
nav button.on{color:var(--text);border-bottom-color:var(--accent);background:var(--panel)}
main{max-width:1180px;margin:0 auto;padding:22px}
section.tab{display:none}section.tab.on{display:block;animation:fade .18s ease}
@keyframes fade{from{opacity:.75;transform:translateY(2px)}to{opacity:1;transform:none}}
h2{font-size:18px;margin:0 0 4px}
p.sub{color:var(--muted);margin:0 0 18px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:16px;display:flex;flex-direction:column;gap:10px}
.card h3{margin:0;font-size:16px;display:flex;align-items:center;gap:8px;word-break:break-all}
.card .desc{color:var(--muted);font-size:13.5px;min-height:1.2em}
.card .target{font-family:var(--mono);font-size:12px;color:var(--dim);word-break:break-all}
.badge{display:inline-block;font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.b-ready{color:var(--info)}.b-connected{color:var(--ok)}.b-needs_auth{color:var(--warn)}.b-missing_env{color:var(--warn)}
.b-disabled{color:var(--dim)}.b-error{color:var(--err)}.b-authorizing{color:var(--accent2)}
.tag{font-size:11px;color:var(--muted);border:1px solid var(--line);padding:1px 7px;border-radius:6px;font-family:var(--mono)}
.btn{background:var(--panel2);border:1px solid var(--line);padding:7px 13px;border-radius:10px;cursor:pointer;transition:.15s;white-space:nowrap}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#1b120d;font-weight:700}
.btn.primary:hover{filter:brightness(1.08)}
.btn.danger:hover{border-color:var(--err);color:var(--err)}
.btn:disabled{opacity:.5;cursor:progress}
.toggle{position:relative;width:42px;height:24px;flex:none}
.toggle input{opacity:0;width:0;height:0}
.toggle span{position:absolute;inset:0;background:#3b2c23;border-radius:999px;cursor:pointer;transition:.2s}
.toggle span:before{content:"";position:absolute;width:18px;height:18px;left:3px;top:3px;background:#d8c7ba;border-radius:50%;transition:.2s}
.toggle input:checked+span{background:var(--accent)}
.toggle input:checked+span:before{transform:translateX(18px);background:#fff}
label.f{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--muted)}
input[type=text],input[type=number],input[type=password],select,textarea{background:#0f0b09;border:1px solid var(--line);border-radius:10px;padding:8px 10px;width:100%}
input:focus,select:focus,textarea:focus{outline:2px solid #d9824f55;border-color:var(--accent)}
textarea{font-family:var(--mono);font-size:13px;resize:vertical}
pre{background:#0f0b09;border:1px solid var(--line);border-radius:12px;padding:12px;margin:0;font-family:var(--mono);font-size:12.5px;
white-space:pre-wrap;word-break:break-word;max-height:520px;overflow:auto}
.pv-line.el{cursor:pointer;color:#ffd9c2}.pv-line.el:hover{background:#2a1c14}
.stats{display:flex;gap:16px;color:var(--muted);font-size:13px;flex-wrap:wrap}
.stats b{color:var(--text)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:18px;margin-bottom:16px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:860px){.two{grid-template-columns:1fr}header .meta{display:none}}
.form-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.slider{display:flex;gap:10px;align-items:center}.slider input[type=range]{flex:1;accent-color:var(--accent)}
table{width:100%;border-collapse:collapse}
td,th{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:14px;vertical-align:middle}
th{color:var(--muted);font-weight:600;font-size:12.5px}
.empty{color:var(--muted);text-align:center;padding:30px;border:1px dashed var(--line);border-radius:var(--radius)}
.modal-bg{position:fixed;inset:0;background:#000a;display:none;align-items:flex-start;justify-content:center;padding:40px 14px;z-index:20;overflow:auto}
.modal-bg.on{display:flex}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:18px;max-width:860px;width:100%;padding:20px;display:flex;flex-direction:column;gap:14px;box-shadow:0 20px 60px #000a}
.modal header2{display:flex}
.modal .top{display:flex;align-items:center;gap:10px}.modal .top h3{margin:0;flex:1;font-size:17px;word-break:break-all}
.presets{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.preset{text-align:left;background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:12px;cursor:pointer}
.preset:hover{border-color:var(--accent)}.preset b{display:block;margin-bottom:3px}.preset small{color:var(--muted)}
.tools{display:flex;flex-direction:column;gap:6px;max-height:300px;overflow:auto}
.tool{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:8px 10px;cursor:pointer}
.tool.on{border-color:var(--accent)}.tool code{font-family:var(--mono);font-size:12px;color:#ffd9c2;word-break:break-word}
.tool div{color:var(--muted);font-size:12.5px}
#toasts{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:50}
.toast{background:#2a1c14;border:1px solid var(--line);border-left:4px solid var(--ok);padding:10px 14px;border-radius:10px;max-width:380px;
box-shadow:0 8px 30px #0008;animation:fade .2s ease;word-break:break-word}
.toast.err{border-left-color:var(--err)}
.spin{display:inline-block;width:14px;height:14px;border:2px solid #fff4;border-top-color:var(--accent);border-radius:50%;animation:sp .7s linear infinite;vertical-align:-2px}
@keyframes sp{to{transform:rotate(360deg)}}
.muted{color:var(--muted)}.mono{font-family:var(--mono)}.card .mono{word-break:break-all}
a{color:var(--accent2)}
footer{color:var(--dim);text-align:center;font-size:12px;padding:20px}
</style>
</head>
<body>
<header>
  <img class="logo" alt="" id="logo">
  <div><h1>Capy<span>Browser</span></h1><div class="meta" id="meta"></div></div>
  <div class="spacer"></div>
  <div class="langs"><button data-lang="en">EN</button> <button data-lang="ru">RU</button></div>
</header>
<nav>
  <button data-tab="hub" class="on" data-i18n="tab_hub"></button>
  <button data-tab="browser" data-i18n="tab_browser"></button>
  <button data-tab="config" data-i18n="tab_config"></button>
  <button data-tab="chat" data-i18n="tab_chat"></button>
</nav>
<main>
<section class="tab on" id="tab-hub">
  <div class="row" style="justify-content:space-between;margin-bottom:14px">
    <div><h2 data-i18n="hub_title"></h2><p class="sub" data-i18n="hub_sub"></p></div>
    <button class="btn primary" id="add-btn" data-i18n="add_connector"></button>
  </div>
  <div id="warnings"></div>
  <div class="grid" id="cards"></div>
</section>

<section class="tab" id="tab-browser">
  <div class="panel">
    <h2 data-i18n="br_title"></h2><p class="sub" data-i18n="br_sub"></p>
    <div class="form-grid">
      <label class="f"><span data-i18n="br_headed"></span><label class="toggle"><input type="checkbox" id="b-headed"><span></span></label></label>
      <label class="f"><span data-i18n="br_files"></span><label class="toggle"><input type="checkbox" id="b-files"><span></span></label></label>
      <label class="f"><span data-i18n="br_lang"></span><input type="text" id="b-lang" placeholder="ru-RU"></label>
      <label class="f"><span data-i18n="br_timeout"></span><input type="number" id="b-timeout" min="1" max="600" step="1"></label>
    </div>
    <div style="margin-top:14px">
      <label class="f"><span><span data-i18n="br_max"></span> — <b id="b-max-val"></b></span></label>
      <div class="slider"><input type="range" id="b-max" min="1000" max="24000" step="250">
        <button class="btn" data-max="3500">4B</button><button class="btn" data-max="6000">8B</button><button class="btn" data-max="12000">32B</button></div>
    </div>
    <div class="form-grid" style="margin-top:14px">
      <label class="f"><span data-i18n="br_profile"></span><input type="text" id="b-profile" placeholder="~/.capybrowser/profile"></label>
      <label class="f"><span data-i18n="br_path"></span><input type="text" id="b-path"></label>
    </div>
    <p class="muted" style="margin:10px 0 0"><span data-i18n="br_detected"></span> <span class="mono" id="b-detected"></span></p>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="b-save" data-i18n="save"></button></div>
  </div>
  <div class="panel">
    <h2 data-i18n="pv_title"></h2><p class="sub" data-i18n="pv_sub"></p>
    <div class="row"><input type="text" id="pv-target" style="flex:1;min-width:240px" placeholder="https://example.com">
      <button class="btn primary" id="pv-go" data-i18n="pv_go"></button></div>
    <div class="row" style="margin-top:10px">
      <input type="number" id="pv-id" placeholder="[id]" style="width:90px">
      <button class="btn" id="pv-click" data-i18n="pv_click"></button>
      <input type="text" id="pv-text" style="flex:1;min-width:160px" data-i18n-ph="pv_text_ph">
      <button class="btn" id="pv-type" data-i18n="pv_type"></button>
      <button class="btn" data-scroll="up">↑</button><button class="btn" data-scroll="down">↓</button>
      <button class="btn" data-scroll="top">⤒</button><button class="btn" data-scroll="bottom">⤓</button>
      <button class="btn" id="pv-back" data-i18n="pv_back"></button>
    </div>
    <div class="stats" style="margin:12px 0 8px" id="pv-stats"></div>
    <pre id="pv-out" class="muted" data-i18n="pv_empty"></pre>
  </div>
</section>

<section class="tab" id="tab-config">
  <div class="panel">
    <h2 data-i18n="env_title"></h2><p class="sub"><span data-i18n="env_sub"></span> <span class="mono" id="env-path"></span></p>
    <table><thead><tr><th data-i18n="env_var"></th><th data-i18n="env_status"></th><th data-i18n="env_value"></th><th></th></tr></thead>
    <tbody id="env-rows"></tbody></table>
    <div class="row" style="margin-top:12px"><input type="text" id="env-new" placeholder="NEW_TOKEN" style="max-width:220px">
      <input type="password" id="env-new-val" style="max-width:300px" autocomplete="off" data-i18n-ph="env_value">
      <button class="btn" id="env-add" data-i18n="env_add"></button></div>
  </div>
  <div class="panel">
    <div class="row" style="justify-content:space-between"><div><h2 data-i18n="cfg_title"></h2>
      <p class="sub mono" id="cfg-path"></p></div>
      <div class="row"><button class="btn" id="cfg-reload" data-i18n="reload"></button><button class="btn" id="cfg-validate" data-i18n="validate"></button>
      <button class="btn primary" id="cfg-save" data-i18n="save"></button></div></div>
    <textarea id="cfg-text" rows="24" spellcheck="false"></textarea>
    <div id="cfg-msg" class="muted" style="margin-top:8px"></div>
  </div>
</section>

<section class="tab" id="tab-chat">
  <h2 data-i18n="chat_title"></h2><p class="sub" data-i18n="chat_sub"></p>
  <div class="grid" id="chat-cards"></div>
</section>
</main>
<footer>CapyBrowser <span id="ver"></span> · <span data-i18n="footer"></span></footer>

<div class="modal-bg" id="modal"><div class="modal" id="modal-box"></div></div>
<div id="toasts"></div>

<script nonce="__NONCE__">
"use strict";
const TOKEN = "__TOKEN__";
const LOGO = __LOGO_JSON__;
const I18N = {
 en:{tab_hub:"Connectors Hub",tab_browser:"Browser & LLM preview",tab_config:"Config & secrets",tab_chat:"Connect to chat",
  hub_title:"Connectors",hub_sub:"Other MCP servers your model reaches through find_tools / call_tool. Changes are saved to capy.yaml and picked up by the running server automatically.",
  add_connector:"+ Add connector",no_connectors:"No connectors yet. Add one from the catalog — GitHub, Notion, files, databases or any MCP server.",
  test:"Test & tools",authorize:"Authorize",edit:"Edit",del:"Delete",enabled:"Enabled",tools:"tools",
  st_ready:"ready",st_connected:"connected",st_needs_auth:"needs auth",st_missing_env:"missing env",st_disabled:"disabled",st_error:"error",st_authorizing:"signing in…",
  missing:"Missing variables:",set_in_config:"set them in Config & secrets",
  catalog:"Add a connector",choose_preset:"Choose a template",back:"Back",add:"Add",cancel:"Cancel",close:"Close",save:"Save",
  confirm_delete:"Delete connector",yes_delete:"Delete",run:"Run",args:"Arguments (JSON)",result:"Result",pick_tool:"Pick a tool to try it.",
  connecting:"Connecting…",open_signin:"Open the sign-in page",auth_hint:"Finish signing in in the browser window, then press Refresh.",refresh:"Refresh",
  br_title:"Browser settings",br_sub:"The browser section of capy.yaml.",br_headed:"Show window (headed)",br_files:"Allow file:// URLs",br_lang:"Language",
  br_timeout:"Page load timeout, s",br_max:"Max characters per answer",br_profile:"Profile folder (keeps logins)",br_path:"Browser executable (optional)",
  br_detected:"Detected browser:",pv_title:"How the model sees a page",pv_sub:"Exactly the text and [id] list a 4B model receives. Click a line with [id] to select it.",
  pv_go:"Preview snapshot",pv_click:"Click [id]",pv_type:"Type",pv_text_ph:"text to type (Enter = submit)",pv_back:"Back",pv_empty:"Enter a URL or a search query.",
  chars:"chars",tokens:"≈ tokens",limit:"limit",
  env_title:"Secrets (.env)",env_sub:"Values are written to",env_var:"Variable",env_status:"Status",env_value:"value",env_add:"Add / update",
  env_set:"set",env_missing:"missing",env_from_env:"from environment",env_from_file:"in .env",env_saved:"Saved to .env",env_removed:"Removed from .env",
  cfg_title:"capy.yaml",reload:"Reload",validate:"Validate",valid:"Valid ✓",saved:"Saved ✓",
  chat_title:"Connect to your chat app",chat_sub:"Ready-made configs with absolute paths for this computer. Copy, paste, restart the app.",
  copy:"Copy",copied:"Copied",where:"Where:",footer:"local panel · everything stays on this computer",
  enabled_on:"Enabled",enabled_off:"Disabled",added:"Connector added",updated:"Saved",deleted:"Deleted",browser_saved:"Browser settings saved"},
 ru:{tab_hub:"Коннекторы",tab_browser:"Браузер и превью",tab_config:"Конфиг и секреты",tab_chat:"Подключить к чату",
  hub_title:"Коннекторы",hub_sub:"Другие MCP-серверы, к которым модель обращается через find_tools / call_tool. Изменения сохраняются в capy.yaml, работающий сервер подхватывает их сам.",
  add_connector:"+ Добавить коннектор",no_connectors:"Коннекторов пока нет. Добавьте из каталога — GitHub, Notion, файлы, базы данных или любой MCP-сервер.",
  test:"Тест и инструменты",authorize:"Авторизовать",edit:"Изменить",del:"Удалить",enabled:"Включён",tools:"инстр.",
  st_ready:"готов",st_connected:"подключён",st_needs_auth:"нужен вход",st_missing_env:"нет переменной",st_disabled:"выключен",st_error:"ошибка",st_authorizing:"вход…",
  missing:"Не заданы переменные:",set_in_config:"задайте их во вкладке «Конфиг и секреты»",
  catalog:"Добавить коннектор",choose_preset:"Выберите шаблон",back:"Назад",add:"Добавить",cancel:"Отмена",close:"Закрыть",save:"Сохранить",
  confirm_delete:"Удалить коннектор",yes_delete:"Удалить",run:"Запустить",args:"Аргументы (JSON)",result:"Результат",pick_tool:"Выберите инструмент, чтобы попробовать его.",
  connecting:"Подключение…",open_signin:"Открыть страницу входа",auth_hint:"Завершите вход в окне браузера и нажмите «Обновить».",refresh:"Обновить",
  br_title:"Настройки браузера",br_sub:"Раздел browser в capy.yaml.",br_headed:"Показывать окно (headed)",br_files:"Разрешить file://",br_lang:"Язык",
  br_timeout:"Таймаут загрузки, с",br_max:"Максимум символов в ответе",br_profile:"Папка профиля (сохраняет входы)",br_path:"Путь к браузеру (необязательно)",
  br_detected:"Найден браузер:",pv_title:"Как модель видит страницу",pv_sub:"Ровно тот текст и список [id], который получит 4B-модель. Нажмите на строку с [id], чтобы выбрать её.",
  pv_go:"Показать снимок",pv_click:"Клик [id]",pv_type:"Ввести",pv_text_ph:"текст для ввода (Enter = отправить)",pv_back:"Назад",pv_empty:"Введите адрес или поисковый запрос.",
  chars:"символов",tokens:"≈ токенов",limit:"лимит",
  env_title:"Секреты (.env)",env_sub:"Значения записываются в",env_var:"Переменная",env_status:"Статус",env_value:"значение",env_add:"Добавить / обновить",
  env_set:"задана",env_missing:"не задана",env_from_env:"из окружения",env_from_file:"в .env",env_saved:"Сохранено в .env",env_removed:"Удалено из .env",
  cfg_title:"capy.yaml",reload:"Перечитать",validate:"Проверить",valid:"Конфиг корректен ✓",saved:"Сохранено ✓",
  chat_title:"Подключение к чат-приложению",chat_sub:"Готовые конфиги с абсолютными путями для этого компьютера. Скопируйте, вставьте, перезапустите приложение.",
  copy:"Копировать",copied:"Скопировано",where:"Куда:",footer:"локальная панель · всё остаётся на этом компьютере",
  enabled_on:"Включён",enabled_off:"Выключен",added:"Коннектор добавлен",updated:"Сохранено",deleted:"Удалено",browser_saved:"Настройки браузера сохранены"}};
let LANG = (()=>{try{return localStorage.getItem("capy_lang")||((navigator.language||"").startsWith("ru")?"ru":"en")}catch(e){return "en"}})();
const t = k => (I18N[LANG]&&I18N[LANG][k]) || I18N.en[k] || k;
let STATE = null;

function el(tag, props, ...kids){
  const n = document.createElement(tag);
  for (const [k,v] of Object.entries(props||{})){
    if (v===null||v===undefined||v===false) continue;
    if (k==="text") n.textContent = v; else if (k==="cls") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v===true?"":v);
  }
  for (const c of kids.flat()) if (c!==null&&c!==undefined&&c!==false) n.append(c instanceof Node?c:document.createTextNode(String(c)));
  return n;
}
function toast(msg, err){
  const n = el("div",{cls:"toast"+(err?" err":""), text:msg});
  document.getElementById("toasts").append(n); setTimeout(()=>n.remove(), err?6500:3200);
}
async function api(method, path, body){
  const opt = {method, headers:{"X-Capy-Token":TOKEN}};
  if (body!==undefined){opt.headers["Content-Type"]="application/json"; opt.body=JSON.stringify(body)}
  const r = await fetch(path, opt);
  let data = {}; try{ data = await r.json() }catch(e){}
  if (!r.ok) throw new Error(data.error || ("HTTP "+r.status));
  return data;
}
async function busy(btn, fn){
  const old = btn ? btn.textContent : null;
  if (btn){btn.disabled=true; btn.textContent=""; btn.append(el("span",{cls:"spin"}))}
  try{ return await fn() } catch(e){ toast(e.message, true) } finally { if(btn){btn.disabled=false; btn.textContent=old} }
}
function applyI18n(){
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach(n=>{ n.textContent = t(n.dataset.i18n) });
  document.querySelectorAll("[data-i18n-ph]").forEach(n=>{ n.placeholder = t(n.dataset.i18nPh) });
  document.querySelectorAll(".langs button").forEach(b=>b.classList.toggle("on", b.dataset.lang===LANG));
}
// ---------------- tabs
document.querySelectorAll("nav button").forEach(b=>b.addEventListener("click",()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("on",x===b));
  document.querySelectorAll("section.tab").forEach(s=>s.classList.toggle("on", s.id==="tab-"+b.dataset.tab));
  if (b.dataset.tab==="config") loadConfigTab(); if (b.dataset.tab==="chat") loadChat(); if (b.dataset.tab==="browser") fillBrowser();
}));
document.querySelectorAll(".langs button").forEach(b=>b.addEventListener("click",()=>{LANG=b.dataset.lang;
  try{localStorage.setItem("capy_lang",LANG)}catch(e){} applyI18n(); render()}));
// ---------------- modal
function openModal(...nodes){ const box=document.getElementById("modal-box"); box.replaceChildren(...nodes); document.getElementById("modal").classList.add("on") }
function closeModal(){ document.getElementById("modal").classList.remove("on"); document.getElementById("modal-box").replaceChildren() }
document.getElementById("modal").addEventListener("click", e=>{ if(e.target.id==="modal") closeModal() });
document.addEventListener("keydown", e=>{ if(e.key==="Escape") closeModal() });
function modalTop(title){ return el("div",{cls:"top"}, el("h3",{text:title}), el("button",{cls:"btn",text:t("close"),onclick:closeModal})) }

// ---------------- hub
async function refresh(){ STATE = await api("GET","/api/state"); render() }
function render(){
  if (!STATE) return;
  document.getElementById("meta").textContent = STATE.config_path;
  document.getElementById("ver").textContent = STATE.version;
  const w = document.getElementById("warnings"); w.replaceChildren();
  for (const msg of [STATE.error, ...(STATE.warnings||[])].filter(Boolean))
    w.append(el("div",{cls:"panel", style:"border-color:#f2b84b66;color:#f2d3a8"}, "⚠ ", msg));
  const cards = document.getElementById("cards"); cards.replaceChildren();
  if (!STATE.connectors.length) cards.append(el("div",{cls:"empty", text:t("no_connectors")}));
  for (const c of STATE.connectors) cards.append(card(c));
}
function card(c){
  const toggle = el("input",{type:"checkbox"}); toggle.checked = c.enabled;
  toggle.addEventListener("change", ()=>busy(null, async()=>{ await api("POST",`/api/connectors/${encodeURIComponent(c.name)}/enabled`,{enabled:toggle.checked});
    toast(toggle.checked?t("enabled_on"):t("enabled_off")); await refresh() }));
  const badge = el("span",{cls:"badge b-"+c.status, text:t("st_"+c.status)});
  const kids = [
    el("h3",{}, el("span",{text:c.name}), el("span",{cls:"tag",text:c.type==="streamable-http"?"http":c.type}), badge),
    el("div",{cls:"desc",text:c.description||""}),
    el("div",{cls:"target",text:c.target})];
  if (c.tools!==null) kids.push(el("div",{cls:"muted",text:`${c.tools} ${t("tools")}`}));
  if (c.missing_vars && c.missing_vars.length) kids.push(el("div",{cls:"muted"}, t("missing")+" ", el("b",{cls:"mono",text:c.missing_vars.join(", ")}), " — "+t("set_in_config")));
  if (c.error) kids.push(el("div",{style:"color:var(--err);font-size:13px", text:c.error}));
  const actions = el("div",{cls:"row"},
    el("label",{cls:"toggle",title:t("enabled")}, toggle, el("span")),
    el("button",{cls:"btn",text:t("test"),onclick:()=>openTest(c)}),
    c.auth==="oauth"?el("button",{cls:"btn",text:t("authorize"),onclick:e=>authorize(c,e.target)}):null,
    el("button",{cls:"btn",text:t("edit"),onclick:()=>openEdit(c)}),
    el("button",{cls:"btn danger",text:t("del"),onclick:()=>confirmDelete(c)}));
  kids.push(actions);
  return el("div",{cls:"card"}, ...kids);
}
function confirmDelete(c){
  openModal(modalTop(t("confirm_delete")+" "+c.name+"?"), el("div",{cls:"row"},
    el("button",{cls:"btn danger",text:t("yes_delete"),onclick:e=>busy(e.target, async()=>{await api("DELETE",`/api/connectors/${encodeURIComponent(c.name)}`);
      closeModal(); toast(t("deleted")); await refresh()})}), el("button",{cls:"btn",text:t("cancel"),onclick:closeModal})));
}
async function openEdit(c){
  const data = await busy(null, ()=>api("GET",`/api/connectors/${encodeURIComponent(c.name)}`)); if(!data) return;
  const ta = el("textarea",{rows:"14",spellcheck:"false"}); ta.value = data.yaml;
  openModal(modalTop(t("edit")+": "+c.name), ta, el("div",{cls:"row"},
    el("button",{cls:"btn primary",text:t("save"),onclick:e=>busy(e.target, async()=>{await api("PUT",`/api/connectors/${encodeURIComponent(c.name)}`,{yaml:ta.value});
      closeModal(); toast(t("updated")); await refresh()})}), el("button",{cls:"btn",text:t("cancel"),onclick:closeModal})));
}
async function authorize(c, btn){
  const r = await busy(btn, ()=>api("POST",`/api/connectors/${encodeURIComponent(c.name)}/authorize`,{})); if(!r) return;
  const body = [modalTop(t("authorize")+": "+c.name), el("p",{cls:"muted",text:t("auth_hint")})];
  if (r.auth_url && /^https?:\/\//.test(r.auth_url)) body.push(el("a",{href:r.auth_url,target:"_blank",rel:"noopener noreferrer",text:t("open_signin")}));
  body.push(el("div",{cls:"row"}, el("button",{cls:"btn primary",text:t("refresh"),onclick:async()=>{closeModal(); await refresh()}})));
  openModal(...body); await refresh();
}
function skeleton(schema){
  const o = {}; const props = (schema&&schema.properties)||{};
  for (const k of (schema&&schema.required)||[]){ const ty=(props[k]||{}).type; o[k] = ty==="number"||ty==="integer"?0:ty==="boolean"?false:ty==="array"?[]:ty==="object"?{}:"" }
  return JSON.stringify(o, null, 2);
}
async function openTest(c){
  const list = el("div",{cls:"tools"}), args = el("textarea",{rows:"6",spellcheck:"false"}), out = el("pre",{cls:"muted",text:t("pick_tool")});
  const stats = el("div",{cls:"stats"}); let current = null;
  const runBtn = el("button",{cls:"btn primary",text:t("run"),disabled:true});
  runBtn.addEventListener("click", ()=>busy(runBtn, async()=>{ let a; try{ a = args.value.trim()?JSON.parse(args.value):{} }catch(e){ a = args.value }
    const r = await api("POST",`/api/connectors/${encodeURIComponent(c.name)}/call`,{tool:current.name, arguments:a});
    out.className=""; out.textContent = r.text; stats.replaceChildren(el("span",{},el("b",{text:String(r.chars)})," "+t("chars")), el("span",{},el("b",{text:String(r.tokens)})," "+t("tokens"))) }));
  openModal(modalTop(t("test")+": "+c.name), el("div",{cls:"muted"}, el("span",{cls:"spin"}), " "+t("connecting")));
  const r = await busy(null, ()=>api("POST",`/api/connectors/${encodeURIComponent(c.name)}/test`,{}));
  await refresh();
  if (!r){ closeModal(); return }
  if (!r.ok){ openModal(modalTop(t("test")+": "+c.name), el("pre",{style:"color:var(--err)",text:r.error})); return }
  for (const tool of r.tools){
    const item = el("div",{cls:"tool"}, el("code",{text:tool.signature}));
    item.addEventListener("click", ()=>{ list.querySelectorAll(".tool").forEach(x=>x.classList.remove("on")); item.classList.add("on");
      current = tool; args.value = skeleton(tool.schema); runBtn.disabled = false; out.className="muted"; out.textContent = tool.description || tool.name });
    list.append(item);
  }
  openModal(modalTop(`${t("test")}: ${c.name} · ${r.tools.length} ${t("tools")}`), list,
    el("label",{cls:"f"}, el("span",{text:t("args")}), args), el("div",{cls:"row"}, runBtn), stats, el("label",{cls:"f"}, el("span",{text:t("result")})), out);
}
// ---------------- add connector
document.getElementById("add-btn").addEventListener("click", ()=>{
  const grid = el("div",{cls:"presets"});
  for (const p of STATE.presets) grid.append(el("button",{cls:"preset",onclick:()=>presetForm(p)}, el("b",{text:p.title}), el("small",{text:p.hint})));
  openModal(modalTop(t("catalog")), el("p",{cls:"muted",text:t("choose_preset")}), grid);
});
function presetForm(p){
  const inputs = {}; const form = el("div",{cls:"form-grid"});
  for (const f of p.fields){
    let inp;
    if (f.choices){ inp = el("select",{}); for (const ch of f.choices){ const o=el("option",{value:ch,text:ch}); if(ch===f.default) o.selected=true; inp.append(o) } }
    else if (f.multiline){ inp = el("textarea",{rows:"3",placeholder:f.placeholder||""}); inp.value=f.default||"" }
    else { inp = el("input",{type:"text",placeholder:f.placeholder||""}); inp.value=f.default||"" }
    inputs[f.key]=inp; form.append(el("label",{cls:"f"}, el("span",{text:f.label}), inp));
  }
  openModal(modalTop(p.title), el("p",{cls:"muted",text:p.hint}), form, el("div",{cls:"row"},
    el("button",{cls:"btn primary",text:t("add"),onclick:e=>busy(e.target, async()=>{ const fields={};
      for (const [k,inp] of Object.entries(inputs)) fields[k]=inp.value;
      await api("POST","/api/connectors",{preset:p.id, fields}); closeModal(); toast(t("added")); await refresh() })}),
    el("button",{cls:"btn",text:t("back"),onclick:()=>document.getElementById("add-btn").click()})));
}
// ---------------- browser tab
const $ = id => document.getElementById(id);
function fillBrowser(){
  if (!STATE) return; const b = STATE.browser, e = STATE.effective;
  $("b-headed").checked = !!e.headed; $("b-files").checked = !!e.allow_local_files;
  $("b-lang").value = b.lang||""; $("b-lang").placeholder = STATE.system_lang||"en-US";
  $("b-timeout").value = e.nav_timeout||25; $("b-max").value = e.max_chars||3500; $("b-max-val").textContent = $("b-max").value;
  $("b-profile").value = b.profile_dir||""; $("b-path").value = b.browser||""; $("b-detected").textContent = STATE.detected_browser||"—";
}
$("b-max").addEventListener("input", ()=>{ $("b-max-val").textContent = $("b-max").value });
document.querySelectorAll("[data-max]").forEach(b=>b.addEventListener("click",()=>{ $("b-max").value=b.dataset.max; $("b-max-val").textContent=b.dataset.max }));
$("b-save").addEventListener("click", e=>busy(e.target, async()=>{
  await api("PUT","/api/browser",{values:{headed:$("b-headed").checked, allow_local_files:$("b-files").checked, lang:$("b-lang").value.trim(),
    nav_timeout:Number($("b-timeout").value)||null, max_chars:Number($("b-max").value), profile_dir:$("b-profile").value.trim(), browser:$("b-path").value.trim()}});
  toast(t("browser_saved")); await refresh(); fillBrowser() }));
function showPreview(r){
  const out = $("pv-out"); out.className=""; out.replaceChildren();
  for (const line of r.text.split("\n")){
    const m = /^\[(\d+)\]/.exec(line);
    const span = el("div",{cls:"pv-line"+(m?" el":""), text:line||" "});
    if (m) span.addEventListener("click", ()=>{ $("pv-id").value = m[1]; $("pv-text").focus() });
    out.append(span);
  }
  $("pv-stats").replaceChildren(el("span",{},el("b",{text:String(r.chars)})," "+t("chars")), el("span",{},el("b",{text:String(r.tokens)})," "+t("tokens")),
    el("span",{}, t("limit")+" ", el("b",{text:String(r.max_chars)})));
}
async function pv(body, btn){ const r = await busy(btn, ()=>api("POST","/api/preview",body)); if (r) showPreview(r) }
$("pv-go").addEventListener("click", e=>pv({action:"open", target:$("pv-target").value}, e.target));
$("pv-target").addEventListener("keydown", e=>{ if(e.key==="Enter") $("pv-go").click() });
$("pv-click").addEventListener("click", e=>pv({action:"click", id:Number($("pv-id").value)}, e.target));
$("pv-type").addEventListener("click", e=>pv({action:"type", id:Number($("pv-id").value), text:$("pv-text").value, submit:false}, e.target));
$("pv-text").addEventListener("keydown", e=>{ if(e.key==="Enter") pv({action:"type", id:Number($("pv-id").value), text:$("pv-text").value, submit:true}, $("pv-type")) });
document.querySelectorAll("[data-scroll]").forEach(b=>b.addEventListener("click", e=>pv({action:"scroll", direction:b.dataset.scroll}, e.target)));
$("pv-back").addEventListener("click", e=>pv({action:"back"}, e.target));
// ---------------- config tab
async function loadConfigTab(){
  const [cfg, env] = await Promise.all([api("GET","/api/config"), api("GET","/api/env")]).catch(e=>{toast(e.message,true); return [null,null]});
  if (!cfg) return;
  $("cfg-path").textContent = cfg.path; $("cfg-text").value = cfg.text; $("cfg-msg").textContent = "";
  $("env-path").textContent = env.path; const rows = $("env-rows"); rows.replaceChildren();
  for (const v of env.vars){
    const val = el("input",{type:"password",autocomplete:"off",placeholder:t("env_value")});
    const status = v.set ? `${t("env_set")} · ${v.source===".env"?t("env_from_file"):t("env_from_env")}` : t("env_missing");
    rows.append(el("tr",{}, el("td",{cls:"mono",text:"${"+v.name+"}"}), el("td",{}, el("span",{cls:"badge "+(v.set?"b-connected":"b-missing_env"),text:status})),
      el("td",{}, val), el("td",{cls:"row"},
        el("button",{cls:"btn",text:t("save"),onclick:e=>busy(e.target, async()=>{ await api("PUT","/api/env",{name:v.name,value:val.value}); toast(t("env_saved")); await loadConfigTab(); await refresh() })}),
        v.in_env_file?el("button",{cls:"btn danger",text:t("del"),onclick:e=>busy(e.target, async()=>{ await api("DELETE",`/api/env/${encodeURIComponent(v.name)}`); toast(t("env_removed")); await loadConfigTab(); await refresh() })}):null)));
  }
}
$("env-add").addEventListener("click", e=>busy(e.target, async()=>{ await api("PUT","/api/env",{name:$("env-new").value.trim(), value:$("env-new-val").value});
  $("env-new").value=""; $("env-new-val").value=""; toast(t("env_saved")); await loadConfigTab(); await refresh() }));
$("cfg-reload").addEventListener("click", ()=>loadConfigTab());
$("cfg-validate").addEventListener("click", e=>busy(e.target, async()=>{ const r = await api("POST","/api/config/validate",{text:$("cfg-text").value});
  $("cfg-msg").style.color = r.ok?"var(--ok)":"var(--err)"; $("cfg-msg").textContent = r.ok ? t("valid")+(r.warnings.length?" · "+r.warnings.join("; "):"") : r.error }));
$("cfg-save").addEventListener("click", e=>busy(e.target, async()=>{ await api("PUT","/api/config",{text:$("cfg-text").value});
  $("cfg-msg").style.color="var(--ok)"; $("cfg-msg").textContent=t("saved"); toast(t("saved")); await refresh() }));
// ---------------- chat tab
async function loadChat(){
  const r = await busy(null, ()=>api("GET","/api/chat")); if(!r) return;
  const grid = $("chat-cards"); grid.replaceChildren();
  for (const c of r.configs){
    const pre = el("pre",{text:c.text});
    const btn = el("button",{cls:"btn primary",text:t("copy")});
    btn.addEventListener("click", async()=>{ try{ await navigator.clipboard.writeText(c.text) }catch(e){
        const ta=el("textarea",{}); ta.value=c.text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove() }
      btn.textContent=t("copied"); setTimeout(()=>btn.textContent=t("copy"),1500) });
    grid.append(el("div",{cls:"card"}, el("h3",{text:c.title}), el("div",{cls:"muted"}, t("where")+" ", el("span",{cls:"mono",text:c.where})), pre, el("div",{cls:"row"},btn)));
  }
}
// ---------------- start
$("logo").src = "data:image/svg+xml;base64," + btoa(LOGO);
applyI18n();
refresh().then(fillBrowser).catch(e=>toast(e.message,true));
setInterval(()=>{ if(document.visibilityState==="visible" && !document.getElementById("modal").classList.contains("on")) refresh().catch(()=>{}) }, 5000);
</script>
</body>
</html>
'''


# --------------------------------------------------------------------------- local web panel: capybrowser ui

class _UIState:
    def __init__(self, cfile: CapyFile, state: ConfigState, token: str):
        self.cfile, self.state, self.token = cfile, state, token
        self.allowed_hosts: set[str] = set()
        self.auth_tasks: dict[str, asyncio.Task] = {}


def _connector_view(ui: _UIState, name: str, spec: dict, raw_spec: Any) -> dict:
    conn = ROUTER.connectors.get(name)
    env_vals = {}
    path = env_path_for(ui.cfile.path, ui.cfile.raw())
    if os.path.isfile(path):
        env_vals = load_dotenv(path)
    needed = referenced_vars(json.dumps(raw_spec, ensure_ascii=False)) if raw_spec is not None else []
    missing = [v for v in needed if not (os.environ.get(v) or env_vals.get(v))]
    auth = (spec.get("auth") or {}).get("type") if spec.get("type") != "stdio" else None
    task = ui.auth_tasks.get(name)
    if not spec["enabled"]:
        status = "disabled"
    elif missing or spec.get("problem"):
        status = "missing_env"
    elif task is not None and not task.done():
        status = "authorizing"
    elif conn is not None and conn.connected:
        status = "connected"
    elif conn is not None and conn.status == "failed":
        status = "error"
    elif auth == "oauth" and not oauth_has_tokens(name, spec):
        status = "needs_auth"
    else:
        status = "ready"
    target = spec.get("url") or " ".join([spec.get("command", "")] + list(spec.get("args", [])))
    return {"name": name, "type": spec["type"], "enabled": spec["enabled"], "status": status, "auth": auth,
            "description": spec.get("description", ""), "target": mask_secrets(_cut(target, 160)),
            "tools": len(conn.tools) if conn is not None and conn.tools else None,
            "error": mask_secrets(_cut(conn.error or "", 300)) if conn is not None and conn.status == "failed" else "",
            "missing_vars": missing, "auth_url": _OAUTH_URLS.get(name) if status == "authorizing" else None}


def _ui_state_json(ui: _UIState) -> dict:
    text = ui.cfile.read()
    raw = ui.cfile.raw(text)
    raw_servers = raw.get("mcp_servers") if isinstance(raw.get("mcp_servers"), dict) else {}
    cfg = ui.state.current
    eff = ui.state.effective()
    exe = find_browser(eff.get("browser"))
    return {
        "version": __version__, "config_path": ui.cfile.path, "env_path": env_path_for(ui.cfile.path, raw),
        "platform": platform.system(), "error": ui.state.error, "warnings": cfg.get("warnings", []),
        "browser": {k: cfg["browser"].get(k) for k in ("headed", "browser", "lang", "max_chars", "nav_timeout",
                                                         "profile_dir", "allow_local_files")},
        "effective": {k: eff.get(k) for k in ("headed", "lang", "max_chars", "nav_timeout", "profile_dir", "allow_local_files")},
        "system_lang": system_lang(), "detected_browser": exe,
        "connectors": [_connector_view(ui, n, s, raw_servers.get(n)) for n, s in cfg["mcp_servers"].items()],
        "presets": PRESETS,
    }


async def _ui_reload(ui: _UIState) -> None:
    await ui.state.maybe_reload()


def build_ui_app(ui: _UIState) -> Any:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Route

    def deny(msg: str, code: int = 403) -> Response:
        return JSONResponse({"error": msg}, status_code=code)

    def host_ok(request: Request) -> bool:
        return (request.headers.get("host") or "").lower() in ui.allowed_hosts

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:  # non-browser clients; the token is still required
            return True
        return origin.lower().removeprefix("http://") in ui.allowed_hosts

    def api(handler: Callable) -> Callable:
        async def wrapped(request: Request) -> Response:
            if not host_ok(request):
                return deny("forbidden host")
            if request.headers.get("x-capy-token") != ui.token:
                return deny("missing or wrong X-Capy-Token")
            if request.method != "GET" and not origin_ok(request):
                return deny("forbidden origin")
            if request.method in ("POST", "PUT") and "application/json" not in (request.headers.get("content-type") or ""):
                return deny("content-type must be application/json", 415)
            try:
                body = await request.json() if request.method in ("POST", "PUT") else {}
                if not isinstance(body, dict):
                    return deny("JSON object expected", 400)
                result = await handler(request, body)
                return JSONResponse(result)
            except ConfigError as e:
                return JSONResponse({"error": mask_secrets(str(e))}, status_code=400)
            except ValueError as e:
                return JSONResponse({"error": mask_secrets(str(e))}, status_code=400)
            except Exception as e:  # pragma: no cover - unexpected
                log.exception("ui api error")
                return JSONResponse({"error": mask_secrets(f"{type(e).__name__}: {e}")}, status_code=500)
        return wrapped

    async def page(request: Request) -> Response:
        if not host_ok(request):
            return Response("Forbidden host", status_code=403)
        given = request.query_params.get("t") or request.cookies.get("capy_ui")
        if given != ui.token:
            return HTMLResponse("<!doctype html><meta charset=utf-8><title>CapyBrowser</title>"
                                "<p style='font-family:sans-serif'>Open the link printed in the terminal "
                                "(it contains the session key).</p>", status_code=403)
        nonce = secrets_token(16)
        html = (UI_HTML.replace("__NONCE__", nonce).replace("__TOKEN__", ui.token)
                .replace("__LOGO_JSON__", json.dumps(UI_LOGO).replace("</", "<\\/")))
        resp = HTMLResponse(html)
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.set_cookie("capy_ui", ui.token, httponly=True, samesite="strict", path="/")
        return resp

    async def state(request: Request, body: dict) -> dict:
        await _ui_reload(ui)
        return _ui_state_json(ui)

    async def add_connector(request: Request, body: dict) -> dict:
        name, spec = preset_spec(str(body.get("preset") or ""), body.get("fields") or {})
        ui.cfile.set_server(name, spec, must_exist=False)
        await _ui_reload(ui)
        return {"ok": True, "name": name}

    async def get_connector(request: Request, body: dict) -> dict:
        import yaml

        name = request.path_params["name"]
        raw = ui.cfile.raw().get("mcp_servers") or {}
        if name not in raw:
            raise ConfigError(f"connector '{name}' does not exist")
        return {"name": name, "yaml": yaml.safe_dump(raw[name], sort_keys=False, allow_unicode=True, default_flow_style=None)}

    async def put_connector(request: Request, body: dict) -> dict:
        import yaml

        name = request.path_params["name"]
        try:
            spec = yaml.safe_load(str(body.get("yaml") or ""))
        except Exception as e:
            raise ConfigError(f"invalid YAML: {e}") from None
        if not isinstance(spec, dict):
            raise ConfigError("the connector must be a mapping (command/args or url)")
        ui.cfile.set_server(name, spec, must_exist=True)
        await _ui_reload(ui)
        return {"ok": True}

    async def delete_connector(request: Request, body: dict) -> dict:
        name = request.path_params["name"]
        ui.cfile.delete_server(name)
        await _ui_reload(ui)
        return {"ok": True}

    async def set_enabled(request: Request, body: dict) -> dict:
        ui.cfile.set_enabled(request.path_params["name"], bool(body.get("enabled")))
        await _ui_reload(ui)
        return {"ok": True}

    def _conn(name: str) -> Connector:
        conn = ROUTER.connectors.get(name)
        if conn is None:
            raise ConfigError(f"connector '{name}' does not exist")
        if not conn.spec["enabled"]:
            raise ConfigError(f"connector '{name}' is disabled - turn it on first")
        return conn

    async def test_connector(request: Request, body: dict) -> dict:
        await _ui_reload(ui)
        conn = _conn(request.path_params["name"])
        try:
            await conn.ensure()
        except ConnectorError as e:
            return {"ok": False, "error": mask_secrets(str(e)), "tools": []}
        tools = [{"name": t.name, "signature": mask_secrets(_signature(f"{conn.name}.{t.name}", t, 400)),
                  "description": mask_secrets(t.description or ""), "schema": t.inputSchema or {}} for t in conn.tools]
        return {"ok": True, "tools": tools}

    async def call_connector(request: Request, body: dict) -> dict:
        conn = _conn(request.path_params["name"])
        text = _limit(await ROUTER.call(f"{conn.name}.{body.get('tool') or ''}", body.get("arguments")))
        return {"text": text, "chars": len(text), "tokens": estimate_tokens(text)}

    async def authorize(request: Request, body: dict) -> dict:
        name = request.path_params["name"]
        conn = _conn(name)
        if (conn.spec.get("auth") or {}).get("type") != "oauth":
            raise ConfigError(f"connector '{name}' does not use OAuth")
        old = ui.auth_tasks.get(name)
        if old is None or old.done():
            await conn.close()
            _OAUTH_URLS.pop(name, None)

            async def run() -> None:
                with contextlib.suppress(Exception):
                    await conn.ensure()

            ui.auth_tasks[name] = asyncio.ensure_future(run())
        for _ in range(30):  # give the provider a moment to produce the sign-in URL
            if _OAUTH_URLS.get(name) or ui.auth_tasks[name].done():
                break
            await asyncio.sleep(0.1)
        return {"started": True, "auth_url": _OAUTH_URLS.get(name), "connected": conn.connected}

    async def put_browser(request: Request, body: dict) -> dict:
        values = body.get("values") or {}
        if not isinstance(values, dict):
            raise ConfigError("values must be an object")
        clean: dict[str, Any] = {}
        for k, v in values.items():
            if k not in ("headed", "browser", "lang", "max_chars", "nav_timeout", "profile_dir", "allow_local_files"):
                raise ConfigError(f"unknown browser option {k}")
            clean[k] = None if v in ("", None) else v
        ui.cfile.set_browser(clean)
        await _ui_reload(ui)
        return {"ok": True}

    async def get_config(request: Request, body: dict) -> dict:
        return {"text": ui.cfile.read(), "path": ui.cfile.path}

    async def validate_config(request: Request, body: dict) -> dict:
        try:
            cfg = ui.cfile.validate(str(body.get("text") or ""))
        except ConfigError as e:
            return {"ok": False, "error": mask_secrets(str(e))}
        return {"ok": True, "warnings": cfg["warnings"], "connectors": list(cfg["mcp_servers"])}

    async def put_config(request: Request, body: dict) -> dict:
        ui.cfile.write(str(body.get("text") or ""))
        await _ui_reload(ui)
        return {"ok": True}

    async def get_env(request: Request, body: dict) -> dict:
        text = ui.cfile.read()
        raw = ui.cfile.raw(text)
        return {"path": env_path_for(ui.cfile.path, raw), "vars": env_status(ui.cfile.path, text, raw)}

    async def put_env(request: Request, body: dict) -> dict:
        value = body.get("value")
        if not isinstance(value, str) or not value:
            raise ConfigError("enter a value")
        set_env_value(ui.cfile.path, ui.cfile.raw(), str(body.get("name") or ""), value)
        ui.state.key = ()  # .env changed: force a reload so ${VAR} picks up the new value
        await _ui_reload(ui)
        return {"ok": True}

    async def delete_env(request: Request, body: dict) -> dict:
        set_env_value(ui.cfile.path, ui.cfile.raw(), request.path_params["name"], None)
        ui.state.key = ()
        await _ui_reload(ui)
        return {"ok": True}

    async def preview(request: Request, body: dict) -> dict:
        action = str(body.get("action") or "open")
        if action == "open":
            target = str(body.get("target") or "").strip()
            if not target:
                raise ConfigError("enter a URL or a search query")
            looks_like_url = bool(re.match(r"^(https?://|about:|data:|file:|localhost[:/]|\d+\.\d+\.\d+\.\d+)", target)) or (
                " " not in target and re.search(r"\.[A-Za-z]{2,}(/|$|:)", target) is not None)
            text = await (tool_open_url(target) if looks_like_url else tool_web_search(target))
        elif action == "click":
            text = await tool_click(body.get("id"))
        elif action == "type":
            text = await tool_type_text(body.get("id"), str(body.get("text") or ""), bool(body.get("submit")))
        elif action == "scroll":
            text = await tool_scroll(str(body.get("direction") or "down"))
        elif action == "back":
            text = await tool_go_back()
        else:
            raise ConfigError("unknown action")
        return {"text": text, "chars": len(text), "tokens": estimate_tokens(text), "max_chars": _CONFIG.get("max_chars")}

    async def chat(request: Request, body: dict) -> dict:
        return {"configs": chat_configs(ui.cfile.path)}

    routes = [
        Route("/", page, methods=["GET"]),
        Route("/api/state", api(state), methods=["GET"]),
        Route("/api/connectors", api(add_connector), methods=["POST"]),
        Route("/api/connectors/{name}", api(get_connector), methods=["GET"]),
        Route("/api/connectors/{name}", api(put_connector), methods=["PUT"]),
        Route("/api/connectors/{name}", api(delete_connector), methods=["DELETE"]),
        Route("/api/connectors/{name}/enabled", api(set_enabled), methods=["POST"]),
        Route("/api/connectors/{name}/test", api(test_connector), methods=["POST"]),
        Route("/api/connectors/{name}/call", api(call_connector), methods=["POST"]),
        Route("/api/connectors/{name}/authorize", api(authorize), methods=["POST"]),
        Route("/api/browser", api(put_browser), methods=["PUT"]),
        Route("/api/config", api(get_config), methods=["GET"]),
        Route("/api/config", api(put_config), methods=["PUT"]),
        Route("/api/config/validate", api(validate_config), methods=["POST"]),
        Route("/api/env", api(get_env), methods=["GET"]),
        Route("/api/env", api(put_env), methods=["PUT"]),
        Route("/api/env/{name}", api(delete_env), methods=["DELETE"]),
        Route("/api/preview", api(preview), methods=["POST"]),
        Route("/api/chat", api(chat), methods=["GET"]),
    ]
    return Starlette(routes=routes)


def secrets_token(n: int = 24) -> str:
    import secrets

    return secrets.token_urlsafe(n)


def _pick_port(host: str, port: int) -> int:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return port
        except OSError:
            pass
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _ui_config_path(explicit: str | None) -> tuple[str, bool]:
    if explicit:
        path = os.path.abspath(os.path.expanduser(explicit))
    elif os.path.isfile("capy.yaml"):
        path = os.path.abspath("capy.yaml")
    elif os.path.isfile(os.path.join(os.path.expanduser("~"), ".capy.yaml")):
        path = os.path.join(os.path.expanduser("~"), ".capy.yaml")
    else:
        path = os.path.abspath("capy.yaml")
    created = False
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(CAPY_YAML_TEMPLATE)
        created = True
    return path, created


def ui_cli(argv: list[str]) -> None:
    global CONFIG_STATE
    ap = argparse.ArgumentParser(prog="capybrowser ui", description="Local web panel for CapyBrowser and capy.yaml")
    ap.add_argument("-c", "--config", default=None, help="capy.yaml to edit (default: ./capy.yaml, then ~/.capy.yaml)")
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="port (a free one is used if it is busy)")
    ap.add_argument("--no-open", action="store_true", help="do not open the panel in the browser")
    ap.add_argument("--log-level", default=os.environ.get("BROWSER_MCP_LOG", "WARNING"))
    args = ap.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_MaskingFilter())
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: listening on {args.host} exposes the panel to your network", file=sys.stderr)
    path, created = _ui_config_path(args.config)
    state = ConfigState(path, {})
    try:
        state.load_initial()
    except (ConfigError, OSError) as e:
        state.error = f"capy.yaml has errors: {mask_secrets(e)}"
        state.key = _config_key(path)
        print(f"warning: {state.error} - fix it in the Config tab", file=sys.stderr)
    CONFIG_STATE = state
    eff = state.effective()
    setup(bool(eff["headed"]), eff["browser"], int(eff["max_chars"]), eff["lang"], bool(eff["allow_local_files"]),
          eff["profile_dir"], eff["nav_timeout"])
    ROUTER.warnings = list(state.current.get("warnings", []))
    ROUTER.connectors = {n: Connector(n, spec) for n, spec in state.current["mcp_servers"].items()}
    port = _pick_port(args.host, args.port)
    token = secrets_token()
    ui = _UIState(CapyFile(path), state, token)
    hosts = {args.host, "127.0.0.1", "localhost"} if args.host in ("127.0.0.1", "localhost") else {args.host}
    ui.allowed_hosts = {f"{h}:{port}" for h in hosts} | ({f"[::1]:{port}"} if args.host == "::1" else set())
    shown = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{shown}:{port}/?t={token}"
    import uvicorn

    class _Server(uvicorn.Server):
        # We handle signals ourselves: uvicorn would re-raise SIGTERM after shutdown and kill the
        # process before the browser and the connector processes are cleaned up.
        def install_signal_handlers(self) -> None:  # uvicorn < 0.29
            pass

        @contextlib.contextmanager
        def capture_signals(self):  # uvicorn >= 0.29
            yield

    server = _Server(uvicorn.Config(build_ui_app(ui), host=args.host, port=port, log_level="warning",
                                    lifespan="off", access_log=False))

    def stop(*_a: Any) -> None:
        if server.should_exit:  # second Ctrl+C: stop immediately
            server.force_exit = True
        server.should_exit = True

    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(Exception):
                signal.signal(sig, stop)
    async def serve() -> None:
        task = asyncio.ensure_future(server.serve())
        try:
            while not server.started and not task.done():
                await asyncio.sleep(0.05)
            if server.started:  # announce the URL only once the port is really listening
                print(f"CapyBrowser UI {__version__}: {url}", flush=True)
                print(f"config: {path}" + (" (created from template)" if created else ""), flush=True)
                print("Press Ctrl+C to stop.", flush=True)
                if not args.no_open:
                    import webbrowser

                    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
            await task
        finally:
            await ROUTER.close_all()
            if _browser is not None:
                with contextlib.suppress(Exception):
                    await _browser.close()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_all()


def main(argv: list[str] | None = None) -> None:
    global CONFIG_STATE
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "init":
        return init_cli(argv[1:])
    if argv and argv[0] == "check":
        return check_cli(argv[1:])
    if argv and argv[0] == "ui":
        return ui_cli(argv[1:])
    ap = argparse.ArgumentParser(prog="capybrowser",
                                 description="CapyBrowser: MCP server that drives the system Chromium-based browser over CDP. "
                                             "Commands: 'capybrowser ui' opens the local control panel, 'capybrowser init' creates capy.yaml, "
                                             "'capybrowser check' validates it.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-c", "--config", default=None,
                    help="capy.yaml path (default: $CAPYBROWSER_CONFIG, ./capy.yaml, ~/.capy.yaml)")
    ap.add_argument("--headed", action="store_true", default=None, help="show the browser window (debugging)")
    ap.add_argument("--browser", default=None, help="path to the browser executable")
    ap.add_argument("--max-chars", type=int, default=None, help=f"max characters per tool response (default {MAX_CHARS})")
    ap.add_argument("--lang", default=None, help="browser UI/Accept-Language, e.g. ru-RU (default: system locale)")
    ap.add_argument("--profile-dir", default=None, help="keep the browser profile in this folder (default: temporary)")
    ap.add_argument("--allow-local-files", action="store_true", default=None, help="allow open_url with file:// URLs")
    ap.add_argument("--log-level", default=os.environ.get("BROWSER_MCP_LOG", "WARNING"))
    args, _ = ap.parse_known_args(argv)
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_MaskingFilter())
    if args.max_chars is not None and args.max_chars < 1:
        sys.exit("--max-chars must be a positive number")
    state = ConfigState(args.config, {"headed": args.headed, "browser": args.browser, "max_chars": args.max_chars,
                                      "lang": args.lang, "profile_dir": args.profile_dir,
                                      "allow_local_files": args.allow_local_files})
    try:
        cfg = state.load_initial()
    except (ConfigError, OSError) as e:
        sys.exit(f"config error: {mask_secrets(e)}")
    CONFIG_STATE = state
    eff = state.effective()
    setup(bool(eff["headed"]), eff["browser"], int(eff["max_chars"]), eff["lang"], bool(eff["allow_local_files"]),
          eff["profile_dir"], eff["nav_timeout"])
    ROUTER.warnings = list(cfg["warnings"])
    ROUTER.connectors = {n: Connector(n, spec) for n, spec in cfg["mcp_servers"].items()}
    if cfg["path"]:
        log.info("using config %s (%d connectors)", cfg["path"], len(ROUTER.connectors))
    for w in cfg["warnings"]:
        log.warning("capy.yaml: %s", w)
    _install_signal_handlers()
    try:
        build_server().run("stdio")
    finally:
        cleanup_all()


if __name__ == "__main__":
    main()
