// Cloudflare Pages advanced mode: one-line installers on the site root.
//   curl -fsSL capybrowser.pages.dev | sh      -> install.sh
//   irm capybrowser.pages.dev | iex            -> install.ps1
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ua = request.headers.get("user-agent") || "";
    if (url.pathname === "/") {
      if (/PowerShell/i.test(ua)) url.pathname = "/install.ps1";
      else if (/^(curl|wget|fetch|httpie)/i.test(ua)) url.pathname = "/install.sh";
      if (url.pathname !== "/") {
        const r = await env.ASSETS.fetch(new Request(url, request));
        return new Response(r.body, { status: r.status, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=300" } });
      }
    }
    return env.ASSETS.fetch(request);
  },
};
