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
        assert names == ["call_tool", "click", "find_tools", "go_back", "list_connectors", "open_url", "scroll",
                         "type_text", "web_search"]
        res = request("tools/call", {"name": "open_url", "arguments": {"url": base + "/form"}})["result"]
        assert "Query" in res["content"][0]["text"]
    finally:
        proc.stdin.close()
        assert proc.wait(30) == 0


# --------------------------------------------------------------------------- capy.yaml, connectors, router

TOKEN = "tok_" + "A1b2C3d4E5f6G7h8"  # fake test token, assembled so secret scanners do not flag it

STDIO_SERVER = r'''
import os, sys
from mcp.server.fastmcp import FastMCP
m = FastMCP("local")

@m.tool()
def send_email(to: str, subject: str, body: str = "") -> str:
    """Send an email message to someone."""
    return f"sent to {to}: {subject}"

@m.tool()
def list_tasks(project: str = "inbox") -> str:
    """List tasks from the todo list."""
    return f"tasks of {project}: buy milk"

@m.tool()
def echo_ru(text: str) -> str:
    """Echo text back (unicode check)."""
    return "Получено: " + text

@m.tool()
def big() -> str:
    """Return a huge answer."""
    return "x" * 200000

@m.tool()
def leak() -> str:
    """Return the secret from the environment."""
    return "secret is " + os.environ.get("LOCAL_SECRET", "")

@m.tool()
def pid() -> int:
    """Process id."""
    return os.getpid()

@m.tool()
def fail() -> str:
    """Always fails."""
    raise ValueError("boom")

m.run("stdio")
'''

HTTP_SERVER = r'''
import sys, uvicorn
from mcp.server.fastmcp import FastMCP
port, token = int(sys.argv[1]), sys.argv[2]
m = FastMCP("remote", host="127.0.0.1", port=port)

@m.tool()
def create_issue(repo: str, title: str) -> str:
    """Create a GitHub issue in a code repository."""
    return f"created {repo}#1: {title}"

@m.tool()
def whoami() -> str:
    """Show the token the server sees."""
    return "authorized with " + token

inner = m.streamable_http_app()

async def app(scope, receive, send):
    if scope["type"] == "http":
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        if auth != "Bearer " + token:
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
    await inner(scope, receive, send)

uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
'''


@pytest.fixture(scope="module")
def http_connector(tmp_path_factory):
    script = tmp_path_factory.mktemp("http") / "http_server.py"
    script.write_text(HTTP_SERVER, encoding="utf-8")
    port = bm.free_port()
    proc = subprocess.Popen([sys.executable, str(script), str(port), TOKEN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import socket
    import time

    deadline = time.time() + 30
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.kill()
    proc.wait(10)


def _write_capy(tmp_path, http_url, max_chars=1500, extra=""):
    (tmp_path / "local_server.py").write_text(STDIO_SERVER, encoding="utf-8")
    (tmp_path / ".env").write_text(f"REMOTE_TOKEN={TOKEN}\nLOCAL_SECRET=s3cr3t-local-value\n", encoding="utf-8")
    cfg = tmp_path / "capy.yaml"
    cfg.write_text(
        "browser:\n  lang: de-DE\n  max_chars: %d\n"
        "mcp_servers:\n"
        "  local:\n    command: %s\n    args: [%s]\n    description: mail and todo helpers\n"
        "    env:\n      LOCAL_SECRET: ${LOCAL_SECRET}\n"
        "  remote:\n    type: streamable-http\n    url: %s\n    description: code hosting\n"
        "    auth:\n      type: bearer\n      token: ${REMOTE_TOKEN}\n%s"
        % (max_chars, json.dumps(sys.executable), json.dumps(str(tmp_path / "local_server.py")), http_url, extra),
        encoding="utf-8")
    return cfg


async def _session_call(cfg_path, scenario, extra_args=()):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env.pop("CAPYBROWSER_CONFIG", None)
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "browser_mcp.py"), "--config", str(cfg_path), *extra_args],
                                   env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, args=None):
                res = await s.call_tool(name, args or {})
                return "".join(getattr(c, "text", "") for c in res.content)

            return await scenario(s, call)


def test_capy_yaml_template_is_valid(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    f = tmp_path / "capy.yaml"
    f.write_text(bm.CAPY_YAML_TEMPLATE, encoding="utf-8")
    cfg = bm.load_config(str(f))
    assert cfg["mcp_servers"] == {} and cfg["browser"]["max_chars"] == 3500 and cfg["browser"]["nav_timeout"] == 25
    assert (ROOT / "capy.yaml").read_text(encoding="utf-8") == bm.CAPY_YAML_TEMPLATE
    # the commented examples are valid too once uncommented
    lines = [ln[1:] if ln.startswith("#  ") or ln.startswith("#    ") else ln for ln in bm.CAPY_YAML_TEMPLATE.splitlines()]
    f.write_text("\n".join(ln.replace("mcp_servers: {}", "mcp_servers:") for ln in lines), encoding="utf-8")
    cfg = bm.load_config(str(f))
    assert set(cfg["mcp_servers"]) == {"time", "files", "github", "notion"}
    assert cfg["mcp_servers"]["github"]["auth"]["type"] == "bearer" and cfg["mcp_servers"]["notion"]["auth"]["type"] == "oauth"
    # ${GITHUB_TOKEN} is not set: the file still loads, only that connector reports the problem
    assert "environment variable" in cfg["mcp_servers"]["github"]["problem"] and "problem" not in cfg["mcp_servers"]["time"]
    r = bm.Router()
    r.connectors = {n: bm.Connector(n, spec) for n, spec in cfg["mcp_servers"].items()}
    out = asyncio.run(r.call("github.anything", {}))
    assert out.startswith("Error") and "environment variable" in out
    assert "failed: auth.token is empty" in r.list_text()


def test_config_defaults_empty_and_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAPYBROWSER_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert bm.load_config(None)["path"] is None
    (tmp_path / "capy.yaml").write_text("", encoding="utf-8")
    cfg = bm.load_config(None)
    assert cfg["path"] == "capy.yaml" and cfg["mcp_servers"] == {} and cfg["browser"] == {}
    with pytest.raises(FileNotFoundError):
        bm.load_config(str(tmp_path / "missing.yaml"))


def test_config_env_and_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPY_TEST_TOKEN", "from-environment")
    (tmp_path / ".env").write_text('# comment\nexport FROM_FILE="quoted value"\nCAPY_TEST_TOKEN=file-loses\nPLAIN=abc # note\n',
                                   encoding="utf-8")
    f = tmp_path / "capy.yaml"
    f.write_text("mcp_servers:\n  x:\n    command: run\n    args: ['${FROM_FILE}', '${PLAIN}']\n"
                 "    env: {T: \"${CAPY_TEST_TOKEN}\", M: \"${NOT_SET_ANYWHERE}\"}\n", encoding="utf-8")
    cfg = bm.load_config(str(f))
    x = cfg["mcp_servers"]["x"]
    assert x["args"] == ["quoted value", "abc"] and x["env"]["T"] == "from-environment" and x["env"]["M"] == ""
    assert any("NOT_SET_ANYWHERE" in w for w in cfg["warnings"])
    assert bm.mask_secrets("value from-environment here") == "value *** here"


@pytest.mark.parametrize("text,needle", [
    ("model:\n  name: x\n", "unknown option"),
    ("browser:\n  colour: red\n", "unknown option"),
    ("browser:\n  max_chars: lots\n", "whole number"),
    ("browser:\n  max_chars: 50\n", "between 200"),
    ("browser:\n  headed: maybe\n", "true or false"),
    ("browser:\n  nav_timeout: 0\n", "between 1"),
    ("browser: [unclosed\n", "invalid YAML"),
    ("- a list\n", "top level"),
    ("mcp_servers:\n  x: {args: [1]}\n", "command"),
    ("mcp_servers:\n  x: {command: a, args: b}\n", "list"),
    ("mcp_servers:\n  x: {command: a, url: 'https://e.com'}\n", "only for remote"),
    ("mcp_servers:\n  x: {url: 'ftp://e.com'}\n", "http"),
    ("mcp_servers:\n  x: {type: websocket, url: 'https://e.com'}\n", "stdio, streamable-http or sse"),
    ("mcp_servers:\n  x: {url: 'https://e.com', auth: {type: bearer}}\n", "token"),
    ("mcp_servers:\n  x: {url: 'https://e.com', auth: {type: magic}}\n", "bearer"),
    ("mcp_servers:\n  x: {url: 'https://e.com', auth: {type: oauth, redirect_port: 80}}\n", "1024"),
    ("mcp_servers:\n  bad name: {command: a}\n", "letters"),
])
def test_config_validation_errors(tmp_path, text, needle):
    f = tmp_path / "capy.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as e:
        bm.load_config(str(f))
    assert needle in str(e.value)


def test_parse_arguments_and_masking():
    assert bm.parse_arguments({"a": 1}) == {"a": 1}
    assert bm.parse_arguments('{"a": 1}') == {"a": 1}
    assert bm.parse_arguments(json.dumps('{"a": 1}')) == {"a": 1}  # double-encoded
    assert bm.parse_arguments("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert bm.parse_arguments("{'a': 1}") == {"a": 1}
    assert bm.parse_arguments(None) == {} and bm.parse_arguments("  ") == {}
    assert bm.parse_arguments("[1, 2]").startswith("Error") and bm.parse_arguments("not json").startswith("Error")
    bm.register_secret("my-configured-secret")
    fake_gh = "ghp_" + "a" * 36
    out = bm.mask_secrets(f"x my-configured-secret y {fake_gh} Authorization: Bearer abc.def.ghi123456")
    assert "my-configured-secret" not in out and fake_gh not in out and "abc.def.ghi123456" not in out


def test_find_tools_ranking_ru_en():
    class T:
        def __init__(self, name, desc, props=None, req=()):
            self.name, self.description = name, desc
            self.inputSchema = {"type": "object", "properties": props or {}, "required": list(req)}

    r = bm.Router()
    c1 = bm.Connector("mail", {"type": "stdio", "enabled": True, "description": "", "timeout": 10})
    c1.tools = [T("send_email", "Send an email message", {"to": {"type": "string"}}, ["to"]), T("list_tasks", "List todo tasks")]
    c2 = bm.Connector("gh", {"type": "sse", "enabled": True, "description": "code hosting", "timeout": 10})
    c2.tools = [T("create_issue", "Create an issue in a repository")]
    for c in (c1, c2):
        c.session = object()
        c._task = type("Running", (), {"done": staticmethod(lambda: False)})()  # "connected" without a transport
    r.connectors = {"mail": c1, "gh": c2}

    def top(q):
        return asyncio.run(r.find(q)).splitlines()[1]

    assert top("отправить письмо").startswith("mail.send_email(to: string)")
    assert top("gmail").startswith("mail.send_email")
    assert top("мои задачи").startswith("mail.list_tasks")
    assert top("notion notes").startswith("mail.list_tasks")
    assert top("код на github").startswith("gh.create_issue")
    assert "No tools match" in asyncio.run(r.find("погода в Москве"))
    assert len(asyncio.run(r.find("", limit=1)).splitlines()) == 3  # header, 1 tool, "+N more"


def test_oauth_storage_and_callback(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPYBROWSER_HOME", str(tmp_path))
    from mcp.shared.auth import OAuthToken

    st = bm._FileTokenStorage("notion", "https://example.com/mcp", {"client_id": "abc", "redirect_uris": ["http://127.0.0.1:1/callback"]})
    assert asyncio.run(st.get_tokens()) is None and asyncio.run(st.get_client_info()).client_id == "abc"
    asyncio.run(st.set_tokens(OAuthToken(access_token="access-token-value-123", token_type="Bearer")))
    assert asyncio.run(st.get_tokens()).access_token == "access-token-value-123"
    assert "access-token-value-123" not in bm.mask_secrets("t=access-token-value-123")
    if os.name != "nt":
        assert (os.stat(st.path).st_mode & 0o777) == 0o600
    cb = bm._OAuthCallback(bm.free_port())
    cb.start()
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    page = opener.open(f"http://127.0.0.1:{cb.port}/callback?code=<script>x</script>&state=s1", timeout=10).read().decode()
    assert "<script>" not in page  # nothing from the URL is reflected
    assert asyncio.run(cb.wait(5)) == ("<script>x</script>", "s1")


@needs_browser
def test_connectors_end_to_end(base, tmp_path, http_connector):
    cfg = _write_capy(tmp_path, http_connector)

    async def scenario(s, call):
        names = [t.name for t in (await s.list_tools()).tools]
        assert names == ["web_search", "open_url", "click", "type_text", "scroll", "go_back",
                         "list_connectors", "find_tools", "call_tool"]
        listing = await call("list_connectors")
        assert "local [stdio]" in listing and "remote [streamable-http]" in listing
        found = await call("find_tools", {"query": "отправить письмо"})
        assert found.splitlines()[1].startswith("local.send_email(to: string, subject: string, body?: string)")
        assert (await call("find_tools", {"query": "github issue"})).splitlines()[1].startswith("remote.create_issue(")
        assert "local.list_tasks" in (await call("find_tools", {"query": "задачи"}))
        assert await call("call_tool", {"tool": "local.send_email", "arguments": {"to": "a@b.c", "subject": "Hi"}}) == "sent to a@b.c: Hi"
        assert await call("call_tool", {"tool": "local.send_email", "arguments": '{"to": "x@y.z", "subject": "JSON"}'}) == "sent to x@y.z: JSON"
        assert await call("call_tool", {"tool": "remote.create_issue", "arguments": {"repo": "me/r", "title": "Баг"}}) == "created me/r#1: Баг"
        assert await call("call_tool", {"tool": "echo_ru", "arguments": {"text": "привет, мир 🙂"}}) == "Получено: привет, мир 🙂"
        big = await call("call_tool", {"tool": "local.big"})
        assert len(big) <= 1500
        leak = await call("call_tool", {"tool": "local.leak"})
        assert "s3cr3t-local-value" not in leak and "***" in leak
        who = await call("call_tool", {"tool": "remote.whoami"})
        assert TOKEN not in who and "***" in who
        failed = await call("call_tool", {"tool": "local.fail"})
        assert failed.startswith("Error") and "boom" in failed
        assert (await call("call_tool", {"tool": "local.nope"})).startswith("Error")
        assert (await call("call_tool", {"tool": "local.send_email", "arguments": "not json"})).startswith("Error: arguments")
        # connector crash -> one automatic reconnect
        pid = int(await call("call_tool", {"tool": "local.pid"}))
        os.kill(pid, 9)
        assert await call("call_tool", {"tool": "local.send_email", "arguments": {"to": "r", "subject": "after crash"}}) == "sent to r: after crash"
        assert int(await call("call_tool", {"tool": "local.pid"})) != pid
        # browser still works and follows capy.yaml settings
        page = await call("open_url", {"url": base + "/long"})
        assert "paragraph 0" in page and len(page) <= 1500
        assert "AL=de-DE" in await call("open_url", {"url": base + "/echo"})
        return pid

    asyncio.run(_session_call(cfg, scenario))


@needs_browser
def test_wrong_token_is_reported_and_masked(tmp_path, http_connector):
    cfg = _write_capy(tmp_path, http_connector)
    (tmp_path / ".env").write_text("REMOTE_TOKEN=wrong-token-value-999\nLOCAL_SECRET=x\n", encoding="utf-8")

    async def scenario(s, call):
        out = await call("call_tool", {"tool": "remote.create_issue", "arguments": {"repo": "a", "title": "b"}})
        assert out.startswith("Error") and "wrong-token-value-999" not in out
        assert "remote [streamable-http] failed" in await call("list_connectors")
        assert await call("call_tool", {"tool": "local.send_email", "arguments": {"to": "a", "subject": "b"}}) == "sent to a: b"

    asyncio.run(_session_call(cfg, scenario))


@needs_browser
def test_hot_reload(base, tmp_path, http_connector):
    import time

    cfg = _write_capy(tmp_path, http_connector, max_chars=3000)

    async def scenario(s, call):
        assert "time2" not in await call("list_connectors")
        assert len(await call("open_url", {"url": base + "/long"})) > 1200
        time.sleep(0.05)
        text = cfg.read_text(encoding="utf-8").replace("max_chars: 3000", "max_chars: 1200")
        text += "  time2:\n    command: %s\n    args: [%s]\n" % (json.dumps(sys.executable), json.dumps(str(tmp_path / "local_server.py")))
        cfg.write_text(text, encoding="utf-8")
        os.utime(cfg, None)
        assert "time2 [stdio]" in await call("list_connectors")
        assert len(await call("scroll", {"direction": "down"})) <= 1200
        cfg.write_text(text + "  broken: {nonsense: 1}\n", encoding="utf-8")
        listing = await call("list_connectors")
        assert "not reloaded" in listing and "time2 [stdio]" in listing  # bad edit keeps the old config
        assert await call("call_tool", {"tool": "time2.send_email", "arguments": {"to": "t", "subject": "s"}}) == "sent to t: s"

    asyncio.run(_session_call(cfg, scenario))


@needs_browser
def test_profile_dir_kept_temp_profile_removed(run, base, tmp_path):
    prof = tmp_path / "profile"
    b = bm.Browser(profile_dir=str(prof), lang="en-US")
    run(b.ensure())
    run(b.navigate(base + "/form"))
    run(b.eval("localStorage.setItem('capy', 'kept')"))
    run(b.close())
    assert prof.is_dir() and any(prof.iterdir())
    b2 = bm.Browser(profile_dir=str(prof), lang="en-US")
    run(b2.ensure())
    run(b2.navigate(base + "/form"))
    assert run(b2.eval("localStorage.getItem('capy')")) == "kept"
    run(b2.close())
    assert prof.is_dir()
    t = bm.Browser(lang="en-US")
    run(t.ensure())
    tmp = t.user_data_dir
    assert tmp and os.path.isdir(tmp)
    run(t.close())
    assert not os.path.exists(tmp)


def _descendants(pid):
    out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True).stdout
    kids, frontier = set(), {pid}
    pairs = [tuple(map(int, ln.split())) for ln in out.splitlines() if len(ln.split()) == 2]
    while frontier:
        nxt = {p for p, pp in pairs if pp in frontier} - kids
        kids |= nxt
        frontier = nxt
    return kids


@needs_browser
@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_signal_kills_browser_and_connectors(tmp_path, http_connector, sig):
    import signal
    import time

    cfg = _write_capy(tmp_path, http_connector)
    proc = subprocess.Popen([sys.executable, str(ROOT / "browser_mcp.py"), "--config", str(cfg)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def request(mid, method, params):
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n").encode())
        proc.stdin.flush()
        while True:
            msg = json.loads(proc.stdout.readline())
            if msg.get("id") == mid:
                return msg

    request(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    proc.stdin.flush()
    request(2, "tools/call", {"name": "find_tools", "arguments": {"query": "email"}})
    request(3, "tools/call", {"name": "open_url", "arguments": {"url": "about:blank"}})
    kids = _descendants(proc.pid)
    assert len(kids) >= 2  # browser tree + stdio connector
    os.kill(proc.pid, getattr(signal, sig))
    proc.wait(20)
    deadline = time.time() + 10
    alive = kids
    while time.time() < deadline:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "stat="], capture_output=True, text=True).stdout
        alive = {int(p) for p, st in (ln.split()[:2] for ln in out.splitlines() if ln.strip()) if int(p) in kids and not st.startswith("Z")}
        if not alive:
            break
        time.sleep(0.2)
    assert not alive, f"left running: {alive}"


def test_init_creates_template(tmp_path):
    target = tmp_path / "capy.yaml"
    out = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "init", str(target)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and target.read_text(encoding="utf-8") == bm.CAPY_YAML_TEMPLATE
    again = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "init", str(target)], capture_output=True, text=True, timeout=60)
    assert again.returncode != 0


def test_check_and_bad_config_cli(tmp_path):
    f = tmp_path / "capy.yaml"
    f.write_text("browser:\n  colour: red\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "--config", str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "unknown option" in out.stderr and "Traceback" not in out.stderr
    f.write_text("mcp_servers:\n  t:\n    command: x\n    env: {K: super-secret-cli-value}\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(ROOT / "browser_mcp.py"), "check", "-c", str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "t [stdio]" in out.stdout and "super-secret-cli-value" not in out.stdout


def test_works_without_config(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    env.pop("CAPYBROWSER_CONFIG", None)
    proc = subprocess.Popen([sys.executable, str(ROOT / "browser_mcp.py")], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                                            "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_connectors", "arguments": {}}}]
    for m in msgs:
        proc.stdin.write((json.dumps(m) + "\n").encode())
    proc.stdin.flush()
    reply = None
    while reply is None:
        msg = json.loads(proc.stdout.readline())
        if msg.get("id") == 2:
            reply = msg
    proc.stdin.close()
    err = proc.stderr.read()
    assert proc.wait(30) == 0 and b"Traceback" not in err
    assert "No connectors configured" in reply["result"]["content"][0]["text"]


def test_config_state_reload_unit(tmp_path):
    import time

    f = tmp_path / "capy.yaml"
    f.write_text("browser:\n  max_chars: 1000\n  nav_timeout: 7\n", encoding="utf-8")
    old_max, old_nav = bm._CONFIG["max_chars"], bm.NAV_TIMEOUT
    try:
        state = bm.ConfigState(str(f), {})
        state.load_initial()
        asyncio.run(bm.apply_config(state))
        assert bm._CONFIG["max_chars"] == 1000 and bm.NAV_TIMEOUT == 7 and not state.changed()
        time.sleep(0.02)
        f.write_text("browser:\n  max_chars: 1200\n", encoding="utf-8")
        assert state.changed()
        asyncio.run(state.maybe_reload())
        assert bm._CONFIG["max_chars"] == 1200 and not state.changed()
        (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")  # .env next to the file is watched too
        assert state.changed()
        asyncio.run(state.maybe_reload())
        f.unlink()  # explicit file removed: the last good config stays
        assert not state.changed()
        asyncio.run(state.maybe_reload())
        assert bm._CONFIG["max_chars"] == 1200
        cli = bm.ConfigState(None, {"max_chars": 900, "headed": None})
        assert cli.effective()["max_chars"] == 900 and cli.effective()["headed"] is False
    finally:
        bm._CONFIG["max_chars"], bm.NAV_TIMEOUT = old_max, old_nav


# --------------------------------------------------------------------------- web panel: capybrowser ui

def test_capyfile_edits_keep_comments(tmp_path):
    f = tmp_path / "capy.yaml"
    f.write_text("# my notes\nbrowser:\n  lang: en-US   # keep me\n  # max_chars: 3500   # model budget\n\n"
                 "mcp_servers:\n  # a comment before mail\n  mail:\n    command: run   # inline\n    args: [a]\n"
                 "  other: {command: x}\n# trailing comment\n", encoding="utf-8")
    cf = bm.CapyFile(str(f))
    cf.set_enabled("mail", False)
    cf.set_enabled("other", False)
    cf.set_browser({"max_chars": 6000, "headed": True, "lang": "ru-RU"})
    cf.set_server("time", {"command": "uvx", "args": ["mcp-server-time"]}, must_exist=False)
    text = f.read_text(encoding="utf-8")
    for keep in ("# my notes", "lang: ru-RU   # keep me", "max_chars: 6000   # model budget", "# a comment before mail",
                 "command: run   # inline", "# trailing comment", "enabled: false"):
        assert keep in text, keep
    cfg = bm.load_config(str(f))
    assert cfg["mcp_servers"]["mail"]["enabled"] is False and cfg["mcp_servers"]["other"]["enabled"] is False
    assert cfg["browser"]["headed"] is True and cfg["mcp_servers"]["time"]["args"] == ["mcp-server-time"]
    with pytest.raises(ValueError):
        cf.set_server("time", {"command": "x"}, must_exist=False)  # duplicate
    with pytest.raises(ValueError):
        cf.set_server("bad", {"url": "ftp://x"}, must_exist=False)  # invalid: nothing written
    assert "bad:" not in f.read_text(encoding="utf-8")
    cf.delete_server("mail")
    cf.delete_server("other")
    cf.delete_server("time")
    assert "mcp_servers: {}" in f.read_text(encoding="utf-8") and bm.load_config(str(f))["mcp_servers"] == {}
    cf.set_server("again", {"command": "y"}, must_exist=False)  # works from the "{}" form too
    assert "again" in bm.load_config(str(f))["mcp_servers"]


def test_presets_produce_valid_config(tmp_path):
    cf = bm.CapyFile(str(tmp_path / "capy.yaml"))
    (tmp_path / "capy.yaml").write_text(bm.CAPY_YAML_TEMPLATE, encoding="utf-8")
    fields = {"remote": {"name": "zap", "url": "https://mcp.zapier.example/mcp", "auth": "bearer", "token_var": "ZAP"},
              "github": {}, "notion": {}, "cloudflare": {}, "linear": {}, "sentry": {}, "filesystem": {"folder": str(tmp_path)}, "postgres": {},
              "sqlite": {"db": str(tmp_path / "x.db")}, "stdio": {"command": "uvx", "args": "mcp-server-time\n--local-timezone\nUTC"},
              "http": {"name": "h", "url": "http://127.0.0.1:9/mcp", "transport": "sse"}}
    for p in bm.PRESETS:
        name, spec = bm.preset_spec(p["id"], fields[p["id"]])
        cf.set_server(name, spec, must_exist=False)
    cfg = bm.load_config(cf.path)
    assert set(cfg["mcp_servers"]) == {"zap", "github", "notion", "cloudflare", "linear", "sentry", "files", "postgres", "sqlite", "local", "h"}
    assert cfg["mcp_servers"]["h"]["type"] == "sse" and cfg["mcp_servers"]["local"]["args"] == ["mcp-server-time", "--local-timezone", "UTC"]
    assert cfg["mcp_servers"]["zap"]["auth"]["type"] == "bearer" and cfg["mcp_servers"]["notion"]["auth"]["type"] == "oauth"
    assert bm.preset_spec("cloudflare", {}) == ("cloudflare", {"type": "streamable-http", "url": "https://mcp.cloudflare.com/mcp",
                                                               "description": bm.OAUTH_REMOTES["cloudflare"][1], "auth": {"type": "oauth"}})
    assert cfg["mcp_servers"]["cloudflare"]["auth"]["type"] == "oauth"
    with pytest.raises(ValueError):
        bm.preset_spec("filesystem", {"folder": ""})
    with pytest.raises(ValueError):
        bm.preset_spec("stdio", {"name": "bad name", "command": "x"})


class UI:
    """Runs `browser_mcp.py ui` in a subprocess and talks to its API like the page does."""

    def __init__(self, cfg_path, extra=()):
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None)
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0  # for CTRL_BREAK_EVENT
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "browser_mcp.py"), "ui", "--config", str(cfg_path), "--no-open", *extra],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=flags)
        line = self.proc.stdout.readline().decode()
        m = re.search(r"(http://127\.0\.0\.1:(\d+))/\?t=(\S+)", line)
        assert m, line + self.proc.stderr.read().decode()
        self.base, self.port, self.token = m.group(1), int(m.group(2)), m.group(3)
        import urllib.request

        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def raw(self, method, path, body=None, headers=None, token=True):
        import urllib.error
        import urllib.request

        h = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            h["X-Capy-Token"] = self.token
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=120) as r:
                return r.status, r.read().decode(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def api(self, method, path, body=None, expect=200):
        if body is None and method in ("POST", "PUT"):
            body = {}
        status, text, _ = self.raw(method, path, body)
        assert status == expect, (status, text)
        return json.loads(text)

    def stop(self, sig=None):
        import signal as _s

        if self.proc.poll() is None:
            # Windows has no SIGINT for other processes: CTRL_BREAK_EVENT arrives as SIGBREAK, which the panel handles
            self.proc.send_signal(sig or (_s.CTRL_BREAK_EVENT if os.name == "nt" else _s.SIGINT))
        try:
            return self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise


def _ui_config(tmp_path):
    (tmp_path / "local_server.py").write_text(STDIO_SERVER, encoding="utf-8")
    f = tmp_path / "capy.yaml"
    f.write_text("# panel test config\nbrowser:\n  lang: de-DE   # german pages\n  max_chars: 2000\n"
                 "mcp_servers:\n  mail:\n    command: %s\n    args: [%s]\n    description: mail and todo\n"
                 % (json.dumps(sys.executable), json.dumps(str(tmp_path / "local_server.py"))), encoding="utf-8")
    (tmp_path / ".git").mkdir()  # pretend the folder is a repository: .env must be git-ignored
    return f


@needs_browser
def test_ui_security_and_page(tmp_path):
    import socket

    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    try:
        ui = UI(_ui_config(tmp_path), ["--port", str(busy.getsockname()[1])])
        assert ui.port != busy.getsockname()[1]  # busy port -> another free port
    finally:
        busy.close()
    try:
        status, html, _ = ui.raw("GET", "/", token=False)
        assert status == 403 and ui.token not in html
        status, html, headers = ui.raw("GET", "/?t=" + ui.token, token=False)
        assert status == 200 and f'const TOKEN = "{ui.token}"' in html and "<script nonce=" in html
        assert "default-src 'none'" in headers["content-security-policy"] and "http" not in html.split("<script")[0].split("<style>")[0]
        assert "cdn" not in html.lower() and "googleapis" not in html  # works offline
        assert "innerHTML" not in html  # user data only through textContent / DOM API
        assert ui.raw("GET", "/?t=wrong", token=False)[0] == 403
        assert ui.raw("GET", "/?t=" + ui.token, token=False, headers={"Host": f"evil.example:{ui.port}"})[0] == 403
        assert ui.raw("GET", "/api/state", token=False)[0] == 403
        assert ui.raw("GET", "/api/state", headers={"X-Capy-Token": "nope"})[0] == 403
        assert ui.raw("GET", "/api/state", headers={"Host": f"rebind.example:{ui.port}"})[0] == 403
        assert ui.raw("PUT", "/api/env", {"name": "X", "value": "1"}, headers={"Origin": "https://evil.example"})[0] == 403
        assert ui.raw("PUT", "/api/env", {"name": "X", "value": "1"}, headers={"Origin": f"http://localhost:{ui.port}"})[0] == 200
        assert ui.raw("POST", "/api/connectors", None)[0] in (400, 415)  # JSON body required
    finally:
        assert ui.stop() == 0


@needs_browser
def test_ui_crud_env_config_preview(base, tmp_path):
    cfg = _ui_config(tmp_path)
    ui = UI(cfg)
    try:
        st = ui.api("GET", "/api/state")
        assert st["version"] == bm.__version__ and st["config_path"] == str(cfg)
        assert [c["name"] for c in st["connectors"]] == ["mail"] and st["connectors"][0]["status"] == "ready"
        assert st["browser"]["lang"] == "de-DE" and len(st["presets"]) == 11
        # add GitHub (bearer ${GITHUB_TOKEN}) -> missing_env until the secret is set
        ui.api("POST", "/api/connectors", {"preset": "github", "fields": {"name": "github", "auth": "bearer", "token_var": "GH_PANEL_TOKEN"}})
        ui.api("POST", "/api/connectors", {"preset": "github", "fields": {"name": "github"}}, expect=400)  # duplicate
        gh = [c for c in ui.api("GET", "/api/state")["connectors"] if c["name"] == "github"][0]
        assert gh["status"] == "missing_env" and gh["missing_vars"] == ["GH_PANEL_TOKEN"]
        secret = "ghp_" + "S" * 36
        ui.api("PUT", "/api/env", {"name": "GH_PANEL_TOKEN", "value": secret})
        env_file = (tmp_path / ".env").read_text(encoding="utf-8")
        assert f"GH_PANEL_TOKEN={secret}" in env_file and ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
        if os.name != "nt":
            assert (os.stat(tmp_path / ".env").st_mode & 0o777) == 0o600
        envs = ui.api("GET", "/api/env")
        assert {"name": "GH_PANEL_TOKEN", "set": True, "source": ".env", "in_env_file": True, "used": True} in envs["vars"]
        for path in ("/api/env", "/api/state", "/api/config", "/api/connectors/github", "/api/chat"):
            assert secret not in ui.raw("GET", path)[1], path  # secret values never come back
        gh = [c for c in ui.api("GET", "/api/state")["connectors"] if c["name"] == "github"][0]
        assert gh["status"] == "ready"
        ui.api("PUT", "/api/env", {"name": "bad name", "value": "x"}, expect=400)
        ui.api("PUT", "/api/env", {"name": "A", "value": "two\nlines"}, expect=400)
        ui.api("DELETE", "/api/env/GH_PANEL_TOKEN")
        assert "GH_PANEL_TOKEN" not in (tmp_path / ".env").read_text(encoding="utf-8")
        # edit, toggle, delete
        y = ui.api("GET", "/api/connectors/github")["yaml"]
        assert "${GH_PANEL_TOKEN}" in y
        ui.api("PUT", "/api/connectors/github", {"yaml": y.replace("GitHub: code", "GitHub repos: code")})
        ui.api("PUT", "/api/connectors/github", {"yaml": "url: ftp://nope"}, expect=400)
        ui.api("POST", "/api/connectors/mail/enabled", {"enabled": False})
        assert [c for c in ui.api("GET", "/api/state")["connectors"] if c["name"] == "mail"][0]["status"] == "disabled"
        ui.api("POST", "/api/connectors/mail/test", expect=400)  # disabled
        ui.api("POST", "/api/connectors/mail/enabled", {"enabled": True})
        ui.api("DELETE", "/api/connectors/github")
        ui.api("DELETE", "/api/connectors/github", expect=400)
        text = cfg.read_text(encoding="utf-8")
        assert "# panel test config" in text and "lang: de-DE   # german pages" in text and "enabled: true" in text
        # live connector test + call through the panel
        r = ui.api("POST", "/api/connectors/mail/test")
        assert r["ok"] and {t["name"] for t in r["tools"]} >= {"send_email", "list_tasks", "big"}
        assert any(t["signature"].startswith("mail.send_email(to: string") for t in r["tools"])
        r = ui.api("POST", "/api/connectors/mail/call", {"tool": "send_email", "arguments": '{"to": "a@b.c", "subject": "Привет"}'})
        assert r["text"] == "sent to a@b.c: Привет" and r["tokens"] > 0
        assert len(ui.api("POST", "/api/connectors/mail/call", {"tool": "big"})["text"]) <= 2000
        assert [c for c in ui.api("GET", "/api/state")["connectors"] if c["name"] == "mail"][0]["status"] == "connected"
        ui.api("POST", "/api/connectors/mail/authorize", expect=400)  # not an OAuth connector
        # config editor: validate + refuse broken YAML
        good = cfg.read_text(encoding="utf-8")
        assert ui.api("POST", "/api/config/validate", {"text": "browser: [oops"})["ok"] is False
        assert ui.api("POST", "/api/config/validate", {"text": good})["ok"] is True
        ui.api("PUT", "/api/config", {"text": "browser:\n  colour: red\n"}, expect=400)
        assert cfg.read_text(encoding="utf-8") == good
        # browser settings keep comments
        ui.api("PUT", "/api/browser", {"values": {"max_chars": 1500, "headed": False, "lang": "en-US", "profile_dir": ""}})
        text = cfg.read_text(encoding="utf-8")
        assert "max_chars: 1500" in text and "lang: en-US   # german pages" in text
        ui.api("PUT", "/api/browser", {"values": {"colour": "red"}}, expect=400)
        # browser preview exactly as the model sees it
        r = ui.api("POST", "/api/preview", {"action": "open", "target": base + "/form"})
        assert "Query" in r["text"] and r["chars"] == len(r["text"]) <= 1500 and r["max_chars"] == 1500
        qid = int(re.search(r"\[(\d+)\] input \"Query\"", r["text"]).group(1))
        r = ui.api("POST", "/api/preview", {"action": "type", "id": qid, "text": "capy", "submit": True})
        assert "q=capy" in r["text"]
        assert "Query" in ui.api("POST", "/api/preview", {"action": "back"})["text"]
        assert "Error" in ui.api("POST", "/api/preview", {"action": "click", "id": 999})["text"]
        ui.api("POST", "/api/preview", {"action": "open", "target": ""}, expect=400)
        # ready-made chat configs
        chat = {c["id"]: c for c in ui.api("GET", "/api/chat")["configs"]}
        assert set(chat) == {"lmstudio", "claude", "codex", "cursor", "openwebui", "ollama"}
        mcp = json.loads(chat["lmstudio"]["text"])["mcpServers"]["capybrowser"]
        assert str(cfg) in mcp["args"] and (os.path.isabs(mcp["command"]) or mcp["command"] == "uv")
        assert chat["codex"]["text"].startswith("[mcp_servers.capybrowser]") and str(cfg).replace("\\", "\\\\") in chat["codex"]["text"]
    finally:
        assert ui.stop() == 0


@needs_browser
@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_ui_stops_cleanly(base, tmp_path, signame):
    import signal as _s
    import time

    ui = UI(_ui_config(tmp_path))
    ui.api("POST", "/api/connectors/mail/test")
    ui.api("POST", "/api/preview", {"action": "open", "target": base + "/form"})
    kids = _descendants(ui.proc.pid)
    assert len(kids) >= 2
    assert ui.stop(getattr(_s, signame)) == 0
    deadline = time.time() + 10
    alive = kids
    while time.time() < deadline and alive:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "stat="], capture_output=True, text=True).stdout
        alive = {int(p) for p, st in (ln.split()[:2] for ln in out.splitlines() if ln.strip()) if int(p) in kids and not st.startswith("Z")}
        time.sleep(0.2)
    assert not alive, alive
    assert not list(tmp_path.glob("browser_mcp_*"))


def test_ui_creates_template_when_missing(tmp_path):
    target = tmp_path / "sub" / "capy.yaml"
    proc = subprocess.Popen([sys.executable, str(ROOT / "browser_mcp.py"), "ui", "--config", str(target), "--no-open", "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert "CapyBrowser UI" in proc.stdout.readline().decode()
        assert "created from template" in proc.stdout.readline().decode()
        assert target.read_text(encoding="utf-8") == bm.CAPY_YAML_TEMPLATE
    finally:
        proc.terminate()
        proc.wait(30)
