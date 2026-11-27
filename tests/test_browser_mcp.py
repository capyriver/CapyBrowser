"""CapyBrowser test suite: pure units, real-browser integration and an MCP stdio smoke test.

    uv run --extra dev pytest -q
Browser tests are skipped automatically when no Chromium-based browser is installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import browser_mcp as bm  # noqa: E402

HAS_BROWSER = bm.find_browser() is not None
needs_browser = pytest.mark.skipif(not HAS_BROWSER, reason="no Chrome/Edge/Brave/Chromium installed")

PAGES = {
    "/form": """<!doctype html><title>Form</title><form action="/echo"><input name="q" placeholder="Query">
<select name="color" aria-label="Color"><option>red</option><option>blue</option></select>
<label><input type="checkbox" name="agree"> Agree</label><button>Send</button></form>""",
    "/form2": """<!doctype html><title>Form2</title><form action="/echo"><input name="a" placeholder="First"><input name="b" placeholder="Second"></form>""",
    "/spa": """<!doctype html><title>SPA</title><input id=c placeholder="Controlled"><p id=m></p><div role=button tabindex=0 id=go>Go</div>
<script>let s='';c.addEventListener('input',e=>{s=e.target.value;m.textContent='STATE:'+s});
go.onclick=()=>{history.pushState({},'','/spa/done');document.body.innerHTML='<h1>DONE '+s+'</h1>'};</script>""",
    "/masks": """<!doctype html><title>Masks</title><input id=phone placeholder="Phone"><script>
phone.addEventListener('input',e=>{let d=e.target.value.replace(/\\D/g,'').slice(0,10),s='';if(d.length>0)s='('+d.slice(0,3);
if(d.length>=3)s+=') '+d.slice(3,6);if(d.length>=6)s+='-'+d.slice(6,8);if(d.length>=8)s+='-'+d.slice(8,10);e.target.value=s;});</script>""",
    "/cards": """<!doctype html><title>Cards</title><div tabindex=0 onclick="document.title='CARD'"><h3>Card</h3><p>CARD_BODY text</p></div>
<button>SHORT_BTN</button><button style="display:none">HIDDEN_BTN</button>""",
    "/popup": """<!doctype html><title>Popup</title><a href="/echo?from=blank" target="_blank">Blank link</a>
<button onclick="window.open('/echo?from=open')">Open window</button>""",
    "/dialog": """<!doctype html><title>Dialog</title><script>alert(1)</script><button onclick="alert(2);this.textContent='ALERTED'">Alert</button>""",
    "/overlay": """<!doctype html><title>Overlay</title><button onclick="document.title='CLICKED'">Covered</button>
<div style="position:fixed;inset:0;z-index:9;background:rgba(0,0,0,.01)"></div>""",
    "/scrollbox": """<!doctype html><title>Box</title><style>html,body{height:100%;overflow:hidden;margin:0}#b{height:100%;overflow:auto}</style>
<div id=b>""" + "".join(f"<p>line {i}</p>" for i in range(300)) + "<p>BOTTOM</p></div>",
    "/long": "<!doctype html><title>Long</title>" + "".join(f"<p>paragraph {i} " + "lorem ipsum " * 20 + "</p>" for i in range(200))
             + "<a href='/echo?end=1'>END_LINK</a>",
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import parse_qs, urlsplit

        u = urlsplit(self.path)
        code = 200
        if u.path == "/echo":
            body = "<title>Echo</title><h1>ECHO</h1>" + "".join(f"<li>{k}={v[0]}</li>" for k, v in parse_qs(u.query).items())
            body += f"<p>AL={self.headers.get('Accept-Language', '')}</p>"
        elif u.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/echo?redirected=1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif u.path in PAGES or u.path.startswith("/spa"):
            body = PAGES.get(u.path, PAGES["/spa"])
        else:
            code, body = 404, "<title>404</title>NOT_FOUND"
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def eid(out: str, pattern: str) -> int:
    for line in out.split("--- ELEMENTS", 1)[-1].splitlines():
        if re.search(pattern, line):
            return int(re.match(r"\[(\d+)\]", line).group(1))
    raise AssertionError(f"no element matching {pattern!r} in:\n{out}")


# --------------------------------------------------------------------------- units


@pytest.mark.parametrize("mc", [1, 10, 200, 3500])
def test_compose_never_exceeds_limit(mc):
    r = random.Random(mc)
    for _ in range(300):
        data = {"url": "u" * r.randint(0, 900), "title": "t" * r.randint(0, 500), "text": "x " * r.randint(0, 5000),
                "elements": [f'[{j}] link "{"y" * r.randint(0, 200)}"' for j in range(1, r.randint(0, 200))],
                "more": r.randint(0, 9), "scrollY": 0, "viewport": 800, "height": r.choice([0, 50000])}
        out = bm.compose(data, mc, r.choice(["", "note " * 50]))
        assert len(out) <= mc
        ids = [int(x) for x in re.findall(r"^\[(\d+)\] ", out.split("--- ELEMENTS", 1)[-1], re.M)]
        assert ids == list(range(1, len(ids) + 1))


def test_compose_handles_garbage():
    for data in (None, [], "x", 5, {"elements": [None, 3, {"a": 1}], "more": "x", "status": "500"}):
        assert isinstance(bm.compose(data, 3500), str)


@pytest.mark.parametrize("url", ["example.com", "https://example.com", "localhost:8080", "127.0.0.1:5000/x", "пример.рф",
                                 "about:blank", "data:text/html,hi", "<https://example.com>"])
def test_normalize_url_good(url):
    assert bm.normalize_url(url)


@pytest.mark.parametrize("url", ["", "   ", None, 5, "javascript:alert(1)", "chrome://settings", "ftp://x.com", "not a url",
                                 "https://exa mple.com", "https://example.com:99999", "file:///etc/passwd", "x" * 9000])
def test_normalize_url_bad(url):
    with pytest.raises(ValueError):
        bm.normalize_url(url)


def test_file_urls_need_opt_in():
    assert bm.normalize_url("file:///tmp/x.html", allow_files=True) == "file:///tmp/x.html"


@pytest.mark.parametrize("value,expected", [(5, 5), ("5", 5), ("[5]", 5), (3.0, 3)])
def test_parse_id_good(value, expected):
    assert bm.parse_id(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", True, 2.5, []])
def test_parse_id_bad(value):
    with pytest.raises(ValueError):
        bm.parse_id(value)


@pytest.mark.parametrize("value,expected", [("ru_RU.UTF-8", "ru-RU"), ("de", "de"), ("es_419", "es-419"),
                                            ("POSIX", None), ("C.UTF-8", None), ("Russian_Russia", None), (None, None)])
def test_normalize_lang(value, expected):
    assert bm.normalize_lang(value) == expected


def test_accept_language():
    assert bm.accept_language("ru-RU") == "ru-RU,ru,en"
    assert bm.accept_language("en-US") == "en-US,en"


def test_clip():
    assert bm.clip("abc", 10) == "abc"
    assert len(bm.clip("x" * 100, 7)) <= 7


def test_pid_alive():
    assert bm._pid_alive(os.getpid())


# --------------------------------------------------------------------------- real browser


@pytest.fixture(scope="module")
def run():
    loop = asyncio.new_event_loop()
    bm.setup(lang="en-US")
    yield loop.run_until_complete
    loop.run_until_complete(bm.get_browser().close())
    loop.close()


@needs_browser
def test_form_flow(run, base):
    out = run(bm.tool_open_url(base + "/form"))
    out = run(bm.tool_type_text(eid(out, '"Query"'), "hello"))
    assert 'value="hello"' in out
    out = run(bm.tool_type_text(eid(out, "select"), "blue"))
    assert 'selected="blue"' in out
    out = run(bm.tool_click(eid(out, "checkbox")))
    assert "(checked)" in out
    out = run(bm.tool_click(eid(out, '"Send"')))
    assert "q=hello" in out and "color=blue" in out and "agree=on" in out
    assert "AL=en-US" in out
    out = run(bm.tool_go_back())
    assert "Query" in out


@needs_browser
def test_enter_submits_without_button(run, base):
    out = run(bm.tool_open_url(base + "/form2"))
    out = run(bm.tool_type_text(eid(out, '"First"'), "one", True))
    assert "a=one" in out


@needs_browser
def test_spa_and_masks(run, base):
    out = run(bm.tool_open_url(base + "/spa"))
    out = run(bm.tool_type_text(eid(out, '"Controlled"'), "abc"))
    assert "STATE:abc" in out
    out = run(bm.tool_click(eid(out, '"Go"')))
    assert "DONE abc" in out and "/spa/done" in out
    out = run(bm.tool_open_url(base + "/masks"))
    run(bm.tool_type_text(eid(out, '"Phone"'), "9991234567"))
    assert run(bm.get_browser().eval("phone.value")) == "(999) 123-45-67"


@needs_browser
def test_cards_hidden_popups_dialogs_overlay(run, base):
    out = run(bm.tool_open_url(base + "/cards"))
    text, elements = out.split("--- ELEMENTS", 1)
    assert "CARD_BODY" in text and "SHORT_BTN" in elements and "HIDDEN_BTN" not in out
    out = run(bm.tool_open_url(base + "/popup"))
    assert "from=blank" in run(bm.tool_click(eid(out, "Blank link")))
    out = run(bm.tool_open_url(base + "/popup"))
    assert "from=open" in run(bm.tool_click(eid(out, "Open window")))
    out = run(bm.tool_open_url(base + "/dialog"))
    assert "ALERTED" in run(bm.tool_click(eid(out, '"Alert"')))
    out = run(bm.tool_open_url(base + "/overlay"))
    run(bm.tool_click(eid(out, "Covered")))
    assert run(bm.get_browser().eval("document.title")) == "CLICKED"


@needs_browser
def test_scrolling(run, base):
    out = run(bm.tool_open_url(base + "/scrollbox"))
    assert "BOTTOM" in run(bm.tool_scroll("bottom"))
    out = run(bm.tool_open_url(base + "/long"))
    assert "END_LINK" not in out and "scroll('down')" in out
    out = run(bm.tool_scroll("bottom"))
    assert "END_LINK" in out and "(end of page)" in out


@needs_browser
def test_errors_and_limits(run, base):
    assert "redirected=1" in run(bm.tool_open_url(base + "/redirect"))
    assert "404" in run(bm.tool_open_url(base + "/missing"))
    assert "ERR_CONNECTION_REFUSED" in run(bm.tool_open_url(f"http://127.0.0.1:{bm.free_port()}/"))
    assert "allow-local-files" in run(bm.tool_open_url("file:///etc/hosts"))
    run(bm.tool_open_url(base + "/form"))
    for bad in (0, -1, 10**9, "abc", None):
        assert run(bm.tool_click(bad)).startswith("Error")
    assert run(bm.tool_scroll("sideways")).startswith("Error")
    assert len(run(bm.tool_open_url(base + "/long"))) <= 3500


@needs_browser
def test_recovers_after_browser_crash(run, base):
    run(bm.tool_open_url(base + "/long"))
    bm.get_browser().kill_sync()
    out = run(bm.tool_scroll("down"))
    assert out.startswith("(Browser was restarted.)") and "paragraph" in out and len(out) <= 3500


# --------------------------------------------------------------------------- MCP protocol


@needs_browser
def test_mcp_stdio_smoke(base):
    proc = subprocess.Popen([sys.executable, str(ROOT / "browser_mcp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    msg_id = 0

    def request(method, params):
        nonlocal msg_id
        msg_id += 1
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}) + "\n").encode())
        proc.stdin.flush()
        while True:
            msg = json.loads(proc.stdout.readline())  # stdout must carry JSON-RPC only
            if msg.get("id") == msg_id:
                return msg

    try:
        request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}})
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        names = sorted(t["name"] for t in request("tools/list", {})["result"]["tools"])
        assert names == ["click", "go_back", "open_url", "scroll", "type_text", "web_search"]
        res = request("tools/call", {"name": "open_url", "arguments": {"url": base + "/form"}})["result"]
        assert "Query" in res["content"][0]["text"]
    finally:
        proc.stdin.close()
        assert proc.wait(30) == 0


# --------------------------------------------------------------------------- capy.yaml + MCP hub

ECHO_SERVER = """
from mcp.server.fastmcp import FastMCP
m = FastMCP("echo")

@m.tool()
def echo(text: str) -> str:
    \"\"\"Echo text back.\"\"\"
    return "ECHO:" + text

@m.tool()
def open_url(url: str) -> str:
    \"\"\"Clashes with the browser tool on purpose.\"\"\"
    return "FAKE"

@m.tool()
def fail() -> str:
    \"\"\"Always fails.\"\"\"
    raise ValueError("boom")

m.run("stdio")
"""


def test_capy_yaml_template_is_valid(tmp_path):
    f = tmp_path / "capy.yaml"
    f.write_text(bm.CAPY_YAML_TEMPLATE, encoding="utf-8")
    cfg = bm.load_config(str(f))
    assert cfg["mcp_servers"] == {} and cfg["browser"]["max_chars"] == 3500 and cfg["path"] == str(f)
    assert (ROOT / "capy.yaml").read_text(encoding="utf-8") == bm.CAPY_YAML_TEMPLATE


def test_capy_yaml_lookup_env_and_validation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAPYBROWSER_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert bm.load_config(None)["path"] is None
    monkeypatch.setenv("CAPY_TEST_TOKEN", "secret123")
    f = tmp_path / "capy.yaml"
    f.write_text("mcp_servers:\n  x:\n    command: run\n    env: {T: \"${CAPY_TEST_TOKEN}\"}\n", encoding="utf-8")
    cfg = bm.load_config(None)
    assert cfg["path"] == "capy.yaml" and cfg["mcp_servers"]["x"]["env"]["T"] == "secret123"
    for bad in ("browser: [unclosed\n", "model:\n  name: x\n", "browser:\n  colour: red\n", "mcp_servers:\n  x: {args: [1]}\n",
                "mcp_servers:\n  x: {command: a, args: b}\n", "- a list\n"):
        f.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            bm.load_config(str(f))
    with pytest.raises(FileNotFoundError):
        bm.load_config(str(tmp_path / "missing.yaml"))


@needs_browser
def test_capy_yaml_hub_end_to_end(base, tmp_path):
    (tmp_path / "echo_server.py").write_text(ECHO_SERVER, encoding="utf-8")
    cfg = tmp_path / "capy.yaml"
    cfg.write_text(
        "browser:\n  lang: de-DE\n  max_chars: 1200\n"
        "mcp_servers:\n"
        f"  echo:\n    command: {json.dumps(sys.executable)}\n    args: [{json.dumps(str(tmp_path / 'echo_server.py'))}]\n"
        "  broken:\n    command: /definitely/not/here\n", encoding="utf-8")

    async def scenario():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "browser_mcp.py"), "--config", str(cfg)],
                                       env=dict(os.environ))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                assert names[:6] == ["web_search", "open_url", "click", "type_text", "scroll", "go_back"]
                assert {"echo", "echo__open_url", "fail"} <= set(names)
                assert (await s.call_tool("echo", {"text": "hi"})).content[0].text == "ECHO:hi"
                assert (await s.call_tool("echo__open_url", {"url": "x"})).content[0].text == "FAKE"
                failed = await s.call_tool("fail", {})
                assert failed.isError and "boom" in failed.content[0].text
                page = (await s.call_tool("open_url", {"url": base + "/long"})).content[0].text
                assert "paragraph 0" in page and len(page) <= 1200
                echo = (await s.call_tool("open_url", {"url": base + "/echo"})).content[0].text
                assert "AL=de-DE" in echo

    asyncio.run(scenario())


def test_init_creates_template(tmp_path):
    target = tmp_path / "capy.yaml"
    out = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "init", str(target)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and target.read_text(encoding="utf-8") == bm.CAPY_YAML_TEMPLATE
    again = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "init", str(target)], capture_output=True, text=True, timeout=60)
    assert again.returncode != 0


def test_bad_config_exits_with_message(tmp_path):
    f = tmp_path / "capy.yaml"
    f.write_text("browser:\n  colour: red\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "--config", str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "unknown option" in out.stderr
