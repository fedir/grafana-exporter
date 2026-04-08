#!/usr/bin/env python3
"""
grafana_export.py — Export Grafana dashboards as self-contained offline HTML files.

Supports two rendering backends (tried in order):
  1. Grafana Image Renderer plugin  (/render/d-solo/…)  — panel-by-panel, best quality
  2. Playwright headless Chromium                        — full-page screenshot fallback

Usage:
  python3 grafana_export.py [OPTIONS] [DASHBOARD_URL …]

Examples:
  # Export one dashboard by URL (time range is extracted automatically)
  python3 grafana_export.py "http://localhost:3000/d/grad-test-matrix/my-dash?from=now-5m&to=now"

  # Export several dashboards using an API token
  python3 grafana_export.py --token glsa_xxx \\
      "http://localhost:3000/d/uid1/dash1" \\
      "http://localhost:3000/d/uid2/dash2"

  # Use basic auth, custom output directory
  python3 grafana_export.py --user admin --password secret --out ./exports \\
      "http://localhost:3000/d/grad-test-matrix/gradient-test"

  # Export ALL dashboards found in Grafana
  python3 grafana_export.py --all --token glsa_xxx

  # Export with a custom output filename
  python3 grafana_export.py --name boss-demo --user admin --password secret \\
      "http://localhost:3000/d/grad-test-matrix/gradient-test"
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def build_opener(user: Optional[str], password: Optional[str], token: Optional[str]):
    """Return a urllib opener with auth headers pre-set."""
    handlers = []
    opener = urllib.request.build_opener(*handlers)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif user and password:
        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    opener.addheaders = list(headers.items())
    return opener


def http_get(opener, url: str, timeout: int = 30) -> bytes:
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} for {url}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error for {url}: {e.reason}") from e


def http_get_json(opener, url: str) -> dict:
    data = http_get(opener, url)
    return json.loads(data)


# ─── Grafana API helpers ───────────────────────────────────────────────────────

def parse_dashboard_url(url: str):
    """Extract (base_url, uid, slug, query_params) from a Grafana dashboard URL."""
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    # /d/<uid>/<slug>
    m = re.match(r"^/d/([^/]+)/([^/?]*)", p.path)
    if not m:
        raise ValueError(f"Cannot parse dashboard UID from URL: {url}")
    uid, slug = m.group(1), m.group(2)
    params = dict(urllib.parse.parse_qsl(p.query))
    return base, uid, slug, params


def fetch_dashboard(opener, base_url: str, uid: str) -> dict:
    url = f"{base_url}/api/dashboards/uid/{uid}"
    return http_get_json(opener, url)


def list_all_dashboards(opener, base_url: str) -> list[dict]:
    url = f"{base_url}/api/search?type=dash-db&limit=5000"
    return http_get_json(opener, url)


def check_image_renderer(opener, base_url: str) -> bool:
    """Return True if Grafana Image Renderer plugin is active."""
    try:
        plugins = http_get_json(opener, f"{base_url}/api/plugins?enabled=1")
        return any(p.get("id") == "grafana-image-renderer" for p in plugins)
    except Exception:
        return False


# ─── Rendering backends ───────────────────────────────────────────────────────

def render_panel_via_api(
    opener, base_url: str, uid: str, slug: str,
    panel_id: int, panel_w: int, panel_h: int,
    time_from: str, time_to: str, tz: str, org_id: str,
    scale: int = 2,
) -> Optional[bytes]:
    """Render a single panel PNG via Grafana Image Renderer."""
    width = max(panel_w * 24, 400)   # grid units → pixels (approx)
    height = max(panel_h * 24, 200)
    params = urllib.parse.urlencode({
        "orgId": org_id,
        "panelId": panel_id,
        "width": width * scale,
        "height": height * scale,
        "from": time_from,
        "to": time_to,
        "tz": tz,
        "scale": scale,
    })
    url = f"{base_url}/render/d-solo/{uid}/{slug}?{params}"
    try:
        return http_get(opener, url, timeout=60)
    except Exception as e:
        print(f"    [warn] render API failed for panel {panel_id}: {e}", file=sys.stderr)
        return None


def render_full_page_playwright(
    url: str,
    out_path: Path,
    width: int = 1920,
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> bool:
    """Screenshot the full Grafana page with Playwright. Returns True on success.

    Credentials are passed via Playwright's http_credentials so they never
    appear in the URL — embedding them in the URL causes browsers to refuse
    fetch() calls with 'Request cannot be constructed from a URL that includes
    credentials'.

    The URL must already contain ?kiosk so that Grafana hides its navigation
    chrome before the page renders. A CSS patch is also injected as a safety
    net for any residual UI elements not suppressed by kiosk mode.
    """
    # CSS that hides every piece of Grafana UI chrome not suppressed by kiosk mode.
    # Selectors cover Grafana v8 through v11 (class names differ across versions).
    HIDE_CHROME_CSS = """
        /* Left navigation sidebar */
        .sidemenu, nav[aria-label="Main menu"],
        [class*="sidemenu"], [class*="SideMenu"],
        [data-testid="navbarmenu"], [class*="NavBar__menuWrapper"],
        /* Top navigation bar */
        .navbar, .main-nav, [class*="NavBar__bar"],
        [class*="navbar-page-btn"], [class*="topnav"],
        /* Dashboard controls row (share / edit / export buttons) */
        [class*="toolbar"], [class*="DashNav"],
        [aria-label="Dashboard controls"],
        [data-testid="data-testid Nav toolbar"],
        /* Page-level header / breadcrumb bar */
        [class*="page-toolbar"], [class*="pageToolbar"],
        /* "Sign in" banner shown to anonymous users */
        [class*="Login"], [class*="login"]
        { display: none !important; }

        /* Remove left padding/margin reserved for the sidebar */
        .main-view, [class*="main-view"], body > div > div > main,
        [class*="scroll-canvas"], [class*="scrollCanvas"]
        { padding-left: 0 !important; margin-left: 0 !important; }
    """

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return False

    print(f"  [playwright] Capturing full page: {url}")
    try:
        with sync_playwright() as pw:
            # In Docker / Kubernetes the kernel user-namespace sandbox is not
            # available, so Chromium must run without it.  Detect containers by
            # checking for the Kubernetes service env var or /.dockerenv.
            in_container = (
                os.environ.get("KUBERNETES_SERVICE_HOST") is not None
                or os.path.exists("/.dockerenv")
            )
            launch_args = ["--no-sandbox", "--disable-setuid-sandbox"] if in_container else []
            browser = pw.chromium.launch(headless=True, args=launch_args)
            ctx_kwargs: dict = {"viewport": {"width": width, "height": 900}}
            if user and password:
                # Use extra_http_headers to send Basic auth on every request
                # (including those made by route.fetch()).  http_credentials only
                # triggers on 401 challenges and can miss the initial route.fetch()
                # call before the session is established.
                creds = base64.b64encode(f"{user}:{password}".encode()).decode()
                ctx_kwargs["extra_http_headers"] = {"Authorization": f"Basic {creds}"}
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()

            # Grafana's HTML contains <base href="http://localhost:PORT/"> derived
            # from its root_url setting.  When Playwright runs inside a container
            # and reaches Grafana via a non-localhost address (e.g. 10.89.0.1:3000),
            # the browser tries to fetch JS/CSS bundles from localhost — which
            # inside a container is the container's own loopback — causing
            # "failed to load its application files".
            #
            # Fix: intercept the HTML document response before Chromium parses it
            # and rewrite <base href="http://localhost:PORT/"> to use the actual
            # origin we navigated to.  This is more reliable than rewriting
            # individual asset requests because it eliminates the mismatch at the
            # source rather than chasing every downstream request.
            nav = urllib.parse.urlparse(url)
            nav_origin = f"{nav.scheme}://{nav.netloc}"

            def _patch_base_href(route):
                if route.request.resource_type == "document":
                    response = route.fetch()
                    body = response.text()  # already decompressed by Playwright
                    # 1. Fix <base href="http://localhost:PORT/">
                    patched = re.sub(
                        r'<base\s+href="[^"]*"',
                        f'<base href="{nav_origin}/"',
                        body,
                    )
                    # 2. Replace every other localhost:PORT reference in the HTML
                    #    (covers grafanaBootData.settings.appUrl and similar JS
                    #    config blocks Grafana embeds in the initial HTML).
                    nav_port_str = str(nav.port or (443 if nav.scheme == "https" else 80))
                    for scheme in ("http", "https"):
                        patched = patched.replace(
                            f"{scheme}://localhost:{nav_port_str}", nav_origin
                        )
                    # Strip Content-Encoding / Content-Length: the body is now
                    # plain text, not gzip/br-encoded.  Keeping the original
                    # encoding header causes the browser to try to decompress the
                    # already-decoded text, corrupting the page.
                    headers = {
                        k: v for k, v in response.headers.items()
                        if k.lower() not in ("content-encoding", "content-length",
                                             "transfer-encoding")
                    }
                    route.fulfill(status=response.status, headers=headers, body=patched)
                else:
                    route.continue_()

            page.route("**/*", _patch_base_href)

            page.goto(url, wait_until="networkidle", timeout=60_000)
            # Wait for panels to finish loading
            try:
                page.wait_for_selector(".panel-container", timeout=15_000)
            except Exception:
                pass
            time.sleep(3)  # let animations/queries settle
            # Inject CSS to hide any residual navigation chrome
            page.add_style_tag(content=HIDE_CHROME_CSS)
            # Expand viewport to full page height before screenshotting
            full_h = page.evaluate("document.body.scrollHeight")
            page.set_viewport_size({"width": width, "height": full_h})
            page.screenshot(path=str(out_path), full_page=True)
            browser.close()
        return True
    except Exception as e:
        print(f"  [playwright] Error: {e}", file=sys.stderr)
        return False


# ─── HTML generation ──────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Grafana Export</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #111217;
      color: #d8d9da;
      font-family: Inter, Helvetica Neue, Arial, sans-serif;
      font-size: 14px;
    }}
    header {{
      background: #181b1f;
      border-bottom: 1px solid #2c3235;
      padding: 12px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
    }}
    header h1 {{ margin: 0; font-size: 18px; font-weight: 500; }}
    header .meta {{ font-size: 12px; color: #9fa7b3; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(24, 1fr);
      gap: 8px;
      padding: 16px;
    }}
    .panel {{
      background: #181b1f;
      border: 1px solid #2c3235;
      border-radius: 4px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}
    .panel-title {{
      padding: 8px 12px 4px;
      font-size: 13px;
      font-weight: 500;
      color: #d8d9da;
      border-bottom: 1px solid #2c3235;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .panel img {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .panel .no-img {{
      padding: 24px;
      text-align: center;
      color: #9fa7b3;
      font-size: 12px;
    }}
    .fullpage-img {{
      display: block;
      width: 100%;
      height: auto;
    }}
    footer {{
      text-align: center;
      padding: 16px;
      font-size: 11px;
      color: #555;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <span class="meta">Exported {exported_at} &nbsp;|&nbsp; {time_from} → {time_to}</span>
  </header>
  {body}
  <footer>Generated by grafana_export.py</footer>
</body>
</html>
"""

PANEL_TMPL = """\
<div class="panel" style="grid-column: span {w}; grid-row: span {h};">
  <div class="panel-title">{title}</div>
  {content}
</div>
"""


def img_tag(png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode()
    return f'<img src="data:image/png;base64,{b64}" alt="panel">'


def build_html_panels(dashboard_meta: dict, panel_images: dict, time_from: str, time_to: str) -> str:
    """Build HTML with one <div> per panel using grid layout."""
    import datetime
    db = dashboard_meta.get("dashboard", {})
    title = db.get("title", "Dashboard")
    exported_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    panels_html = []
    panels = db.get("panels", [])
    # Flatten rows (Grafana v5 row panels contain nested panels)
    flat_panels = []
    for p in panels:
        if p.get("type") == "row":
            flat_panels.extend(p.get("panels", []))
        else:
            flat_panels.append(p)

    for panel in flat_panels:
        pid = panel.get("id")
        ptitle = panel.get("title", f"Panel {pid}")
        grid = panel.get("gridPos", {})
        w = max(grid.get("w", 12), 1)
        h = max(grid.get("h", 8), 1)
        # Scale row-height: each grid unit ≈ 30px in Grafana, but we use CSS rows
        row_span = max(round(h / 3), 1)

        png = panel_images.get(pid)
        content = img_tag(png) if png else '<div class="no-img">Panel image not available</div>'
        panels_html.append(PANEL_TMPL.format(title=ptitle, w=w, h=row_span, content=content))

    body = f'<div class="grid">{"".join(panels_html)}</div>'
    return HTML_TEMPLATE.format(
        title=title,
        exported_at=exported_at,
        time_from=time_from,
        time_to=time_to,
        body=body,
    )


def build_html_fullpage(title: str, png_path: Path, time_from: str, time_to: str) -> str:
    """Build HTML wrapping a single full-page screenshot."""
    import datetime
    exported_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    png_bytes = png_path.read_bytes()
    b64 = base64.b64encode(png_bytes).decode()
    body = f'<img class="fullpage-img" src="data:image/png;base64,{b64}" alt="{title}">'
    return HTML_TEMPLATE.format(
        title=title,
        exported_at=exported_at,
        time_from=time_from,
        time_to=time_to,
        body=body,
    )


# ─── Core export logic ────────────────────────────────────────────────────────

def safe_filename(s: str) -> str:
    # Normalise unicode dashes/hyphens to ASCII hyphen
    s = re.sub(r"[\u2013\u2014\u2012\u2010]", "-", s)
    # Replace any remaining non-word, non-hyphen character with underscore
    s = re.sub(r"[^\w\-]", "_", s)
    # Collapse runs of underscores/hyphens and strip from edges
    s = re.sub(r"[-_]{2,}", "-", s).strip("-_")
    return s


def export_dashboard(
    opener,
    base_url: str,
    uid: str,
    slug: str,
    url_params: dict,
    out_dir: Path,
    has_renderer: bool,
    use_playwright: bool,
    original_url: str,
    playwright_user: Optional[str] = None,
    playwright_password: Optional[str] = None,
    playwright_token: Optional[str] = None,
    output_name: Optional[str] = None,
) -> Path:
    time_from = url_params.get("from", "now-6h")
    time_to = url_params.get("to", "now")
    tz = url_params.get("timezone", "browser")
    org_id = url_params.get("orgId", "1")

    print(f"\n[export] Dashboard UID={uid}  from={time_from}  to={time_to}")

    # Fetch dashboard metadata
    meta = fetch_dashboard(opener, base_url, uid)
    db_title = meta.get("dashboard", {}).get("title", uid)

    if output_name:
        # Strip .html suffix if provided — we always add it ourselves
        stem = output_name[:-5] if output_name.lower().endswith(".html") else output_name
        out_file = out_dir / f"{stem}.html"
    else:
        out_file = out_dir / f"{safe_filename(db_title)}.html"

    if has_renderer:
        # Panel-by-panel rendering
        panels = meta.get("dashboard", {}).get("panels", [])
        flat = []
        for p in panels:
            if p.get("type") == "row":
                flat.extend(p.get("panels", []))
            else:
                flat.append(p)

        print(f"  Rendering {len(flat)} panels via Grafana Image Renderer …")
        panel_images: dict[int, bytes] = {}
        for panel in flat:
            pid = panel.get("id")
            ptype = panel.get("type", "")
            if ptype in ("row", "text", "dashlist", "news"):
                continue
            ptitle = panel.get("title", f"panel-{pid}")
            grid = panel.get("gridPos", {})
            pw = grid.get("w", 12)
            ph = grid.get("h", 8)
            print(f"    Panel [{pid}] {ptitle!r} …", end=" ", flush=True)
            png = render_panel_via_api(
                opener, base_url, uid, slug, pid, pw, ph,
                time_from, time_to, tz, org_id,
            )
            if png:
                panel_images[pid] = png
                print("ok")
            else:
                print("FAILED")

        html = build_html_panels(meta, panel_images, time_from, time_to)
        out_file.write_text(html, encoding="utf-8")
        print(f"  Saved: {out_file}")
        return out_file

    elif use_playwright:
        # Add ?kiosk so Grafana hides its navigation chrome (sidebar, top bar,
        # dashboard toolbar) before the page renders.  Credentials go via
        # http_credentials — never embedded in the URL (see build constraints).
        parsed = urllib.parse.urlparse(original_url)
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        qs["kiosk"] = "1"
        kiosk_url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs)))

        tmp_png = out_dir / f"{safe_filename(db_title)}_tmp.png"
        ok = render_full_page_playwright(
            kiosk_url, tmp_png,
            user=playwright_user,
            password=playwright_password,
        )
        if ok and tmp_png.exists():
            html = build_html_fullpage(db_title, tmp_png, time_from, time_to)
            out_file.write_text(html, encoding="utf-8")
            tmp_png.unlink(missing_ok=True)
            print(f"  Saved: {out_file}")
            return out_file
        else:
            raise RuntimeError("Playwright rendering failed and Image Renderer not available.")

    else:
        raise RuntimeError(
            "No rendering backend available.\n"
            "Install grafana-image-renderer plugin OR run: pip install playwright && playwright install chromium"
        )


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Export Grafana dashboards as self-contained offline HTML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("urls", nargs="*", metavar="DASHBOARD_URL",
                    help="Full Grafana dashboard URL(s) to export")
    ap.add_argument("--all", action="store_true",
                    help="Export ALL dashboards found in Grafana")
    ap.add_argument("--base-url", default="http://localhost:3000",
                    help="Grafana base URL (default: http://localhost:3000)")
    ap.add_argument("--token", default=os.environ.get("GRAFANA_TOKEN"),
                    help="Grafana service account / API token (or set GRAFANA_TOKEN env var)")
    ap.add_argument("--user", default=os.environ.get("GRAFANA_USER", "admin"),
                    help="Grafana username for basic auth (default: admin)")
    ap.add_argument("--password", default=os.environ.get("GRAFANA_PASSWORD", "admin"),
                    help="Grafana password for basic auth (default: admin)")
    ap.add_argument("--from", dest="time_from", default=None,
                    help="Override time range start (e.g. now-1h, 2024-01-01T00:00:00)")
    ap.add_argument("--to", dest="time_to", default=None,
                    help="Override time range end (e.g. now)")
    ap.add_argument("--out", default="./grafana-export",
                    help="Output directory (default: ./grafana-export)")
    ap.add_argument("--backend", choices=["auto", "renderer", "playwright"], default="auto",
                    help="Rendering backend: auto (try renderer first), renderer, playwright")
    ap.add_argument("--width", type=int, default=1920,
                    help="Playwright viewport width in pixels (default: 1920)")
    ap.add_argument("--name", default=None, metavar="FILENAME",
                    help="Output filename (without path). Only valid when exporting a single "
                         "dashboard. The .html extension is added automatically if omitted.")

    args = ap.parse_args()

    if not args.urls and not args.all:
        ap.print_help()
        sys.exit(0)

    if args.name and (args.all or len(args.urls) > 1):
        ap.error("--name can only be used when exporting a single dashboard URL")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    opener = build_opener(args.user, args.password, args.token)

    # Detect rendering backends
    if args.backend == "renderer":
        has_renderer, use_playwright = True, False
    elif args.backend == "playwright":
        has_renderer, use_playwright = False, True
    else:  # auto
        print("Detecting rendering backend …")
        # Use the base_url from first URL arg or --base-url
        probe_base = args.base_url
        if args.urls:
            try:
                probe_base, _, _, _ = parse_dashboard_url(args.urls[0])
            except Exception:
                pass
        has_renderer = check_image_renderer(opener, probe_base)
        if has_renderer:
            print("  ✓ Grafana Image Renderer plugin found — using panel-by-panel rendering")
        else:
            print("  ✗ Grafana Image Renderer not found")
            try:
                from playwright.sync_api import sync_playwright  # noqa: F401
                use_playwright = True
                print("  ✓ Playwright found — using full-page screenshot fallback")
            except ImportError:
                use_playwright = False
                print("  ✗ Playwright not installed")

        if not has_renderer and not use_playwright:
            print(
                "\nERROR: No rendering backend available.\n"
                "Install one of:\n"
                "  A) Grafana Image Renderer plugin (recommended for panel-level export)\n"
                "     https://grafana.com/grafana/plugins/grafana-image-renderer/\n"
                "  B) Playwright:  pip install playwright && playwright install chromium\n",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build list of (base_url, uid, slug, params, original_url) to export
    jobs = []

    if args.urls:
        for raw_url in args.urls:
            base, uid, slug, params = parse_dashboard_url(raw_url)
            if args.time_from:
                params["from"] = args.time_from
            if args.time_to:
                params["to"] = args.time_to
            jobs.append((base, uid, slug, params, raw_url))

    if args.all:
        base = args.base_url
        print(f"\nFetching dashboard list from {base} …")
        dashboards = list_all_dashboards(opener, base)
        print(f"  Found {len(dashboards)} dashboard(s)")
        for d in dashboards:
            uid = d.get("uid", "")
            slug = d.get("url", f"/d/{uid}/x").rstrip("/").split("/")[-1]
            params = {
                "orgId": str(d.get("orgId", 1)),
                "from": args.time_from or "now-1h",
                "to": args.time_to or "now",
            }
            original_url = f"{base}/d/{uid}/{slug}?" + urllib.parse.urlencode(params)
            jobs.append((base, uid, slug, params, original_url))

    exported = []
    failed = []
    for i, (base, uid, slug, params, original_url) in enumerate(jobs):
        try:
            out_file = export_dashboard(
                opener=opener,
                base_url=base,
                uid=uid,
                slug=slug,
                url_params=params,
                out_dir=out_dir,
                has_renderer=has_renderer,
                use_playwright=use_playwright,
                original_url=original_url,
                playwright_user=args.user,
                playwright_password=args.password,
                playwright_token=args.token,
                output_name=args.name if i == 0 else None,
            )
            exported.append(out_file)
        except Exception as e:
            print(f"  [ERROR] {uid}: {e}", file=sys.stderr)
            failed.append((uid, str(e)))

    print(f"\n{'='*60}")
    print(f"Exported {len(exported)} dashboard(s) to: {out_dir.resolve()}")
    for f in exported:
        print(f"  {f.name}")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for uid, err in failed:
            print(f"  {uid}: {err}")
    print()


if __name__ == "__main__":
    main()
