# AGENTS.md

Guidelines for AI coding agents (Copilot, Claude, Cursor, etc.) working in this repository.

---

## Project overview

`grafana_export.py` is a single-file Python script that exports Grafana dashboards as self-contained offline HTML files. `setup.sh` installs dependencies on Linux and macOS.

Key design constraints:
- **No third-party runtime dependencies** in the main script beyond the Python stdlib. `playwright` is an optional fallback, imported lazily inside `render_full_page_playwright`.
- The script must work on **Python 3.9+** (no walrus operator in type hints, no 3.10+ match statements).
- Output must be a **single `.html` file per dashboard** — no external assets, no JavaScript frameworks.
- Must run on **Linux** (Ubuntu, Debian, Mint, Fedora, RHEL, Arch, and others) and **macOS**.

---

## Repository layout

```
grafana_export.py   # Main script — all export logic lives here
setup.sh            # Dependency installer (venv + Playwright + system libs)
Dockerfile          # Container image definition (python:3.12-slim + Playwright)
README.md           # End-user documentation
AGENTS.md           # This file
k8s/
  secret.yaml       # Grafana credentials Secret
  configmap.yaml    # Grafana URL + export config ConfigMap
  pvc.yaml          # PersistentVolumeClaim for HTML output
  job.yaml          # One-shot export Job
  kustomization.yaml# kubectl apply -k k8s/ entry point
grafana-export/     # Default output directory (git-ignored, created at runtime)
.venv/              # Python virtual environment (git-ignored, created by setup.sh)
```

---

## Code style

- Follow **PEP 8**. Line length limit: 100 characters.
- Use type hints on all public functions.
- Prefer `urllib` (stdlib) over `requests` for HTTP. Do not add new third-party imports to `grafana_export.py` without a very strong reason.
- Use `Optional[X]` not `X | None` (Python 3.9 compatibility).
- Functions that interact with the Grafana API belong in the `# ─── Grafana API helpers` section.
- HTML generation belongs in the `# ─── HTML generation` section.
- Keep `main()` thin — argument parsing and orchestration only.

---

## Key functions

| Function | File | Purpose |
|---|---|---|
| `build_opener` | `grafana_export.py` | Build a `urllib` opener with auth headers |
| `http_get` / `http_get_json` | `grafana_export.py` | Raw HTTP GET helpers |
| `parse_dashboard_url` | `grafana_export.py` | Extract UID, slug, query params from a Grafana URL |
| `fetch_dashboard` | `grafana_export.py` | `GET /api/dashboards/uid/<uid>` |
| `list_all_dashboards` | `grafana_export.py` | `GET /api/search?type=dash-db` |
| `check_image_renderer` | `grafana_export.py` | Detect if Image Renderer plugin is active |
| `render_panel_via_api` | `grafana_export.py` | Render one panel PNG via `/render/d-solo/` |
| `render_full_page_playwright` | `grafana_export.py` | Full-page screenshot via Playwright |
| `build_html_panels` | `grafana_export.py` | Assemble per-panel HTML (renderer path) |
| `build_html_fullpage` | `grafana_export.py` | Assemble single-image HTML (Playwright path) |
| `safe_filename` | `grafana_export.py` | Sanitise dashboard title for use as a filename |
| `export_dashboard` | `grafana_export.py` | Orchestrate one dashboard export end-to-end |
| `main` | `grafana_export.py` | CLI entry point |

---

## Playwright authentication — critical constraint

**Never embed credentials in the URL** passed to Playwright (i.e. do not construct `http://user:pass@host/…`).

Modern browsers refuse `fetch()` calls whose URL contains a username or password, which causes Grafana's frontend to fail with:

> `Failed to execute 'fetch' on 'Window': Request cannot be constructed from a URL that includes credentials`

The correct approach is to pass credentials via Playwright's `http_credentials` context option:

```python
ctx = browser.new_context(
    http_credentials={"username": user, "password": password},
    viewport={"width": width, "height": 900},
)
```

This makes Playwright send a `Basic` auth header on every request while keeping the URL clean, so the frontend JavaScript can construct its own fetch requests without hitting the security restriction.

**API tokens and Playwright**: a service account token cannot be injected into a browser session. If anonymous access is disabled and only a token is available, the Playwright path will land on the Grafana login page. Do not try to automate the login form — document the limitation instead.

---

## Filename sanitisation

`safe_filename()` applies three transforms in order:

1. Unicode dashes (`—`, `–`, etc.) → ASCII hyphen `-`
2. Any remaining non-word, non-hyphen character → `_`
3. Consecutive `-` / `_` collapsed to a single `-`, stripped from edges

This preserves readability (e.g. `Gradient Test — Full Matrix` → `Gradient_Test-Full_Matrix`) while staying safe for all filesystems.

---

## Adding a new rendering backend

1. Write a function `render_<name>(…) -> bool` analogous to `render_full_page_playwright`.
2. Add detection logic in `main()` inside the `auto` branch.
3. Thread the new flag through `export_dashboard` alongside `has_renderer` / `use_playwright`.
4. Add `"<name>"` to the `--backend` choices list.
5. Document it in `README.md`.

---

## Adding support for a new output format (PDF, PNG, etc.)

- Add a `--format` flag to the CLI (`html` | `pdf` | `png`).
- For PDF: use `playwright`'s `page.pdf()` instead of `page.screenshot()` in `render_full_page_playwright`.
- For panel-level formats: write the raw PNG bytes directly to disk instead of embedding them in HTML.
- Keep the HTML path working unchanged — it is the primary output.

---

## Testing guidelines

There is no automated test suite yet. When making changes:

1. **Syntax check**: `python3 -m py_compile grafana_export.py`
2. **CLI help**: `python3 grafana_export.py --help` — must exit 0 and print usage.
3. **Live export**: run against `http://localhost:3000` with `--user`/`--password` and open the resulting HTML in a browser. Verify panels are visible and no JavaScript errors appear in the browser console.
4. **Credential injection check**: confirm the URL logged by `[playwright]` does **not** contain `user:password@`. Any credential leak here will break the export silently.
5. **No-backend path**: uninstall or rename the Playwright package and confirm the error message clearly explains what to install.

---

## Common pitfalls

- **Linux system libraries**: Playwright's bundled Chromium requires a set of shared libraries that may not be present on minimal installs. Always use `python3 -m playwright install-deps chromium` (requires sudo) — it detects the distro and calls the right package manager automatically. Do not maintain a manual list of packages in the code; let Playwright manage it.
- **Playwright auth**: pass `user`/`password` via `http_credentials` — see the dedicated section above. Do **not** inject credentials into the URL netloc.
- **API token + Playwright**: tokens work fine with the Image Renderer backend (sent as an HTTP header by `build_opener`). They cannot authenticate a headless browser session — use basic auth (`--user`/`--password`) when the Playwright backend is active.
- **Chromium sandbox in containers**: Docker and Kubernetes do not expose the kernel user-namespace sandbox that Chromium uses by default. The script detects containers automatically (`KUBERNETES_SERVICE_HOST` env var or `/.dockerenv`) and passes `--no-sandbox --disable-setuid-sandbox` to the browser. Do not remove this detection logic — without it Chromium will crash silently on launch.
- **Grafana base-href / localhost rewrite (partial fix)**: When Playwright navigates to Grafana via a non-localhost address (e.g. `10.89.0.1:3000` from inside a K8s pod), Grafana's `root_url = http://localhost:3000` causes the React boot configuration to reference a host the container cannot reach. The script intercepts the HTML document response via `page.route()`, rewrites `<base href>` and all `localhost:PORT` occurrences to the navigation origin, and strips `Content-Encoding` so the modified plain-text body is not double-decoded by the browser. This resolves the issue when `root_url` is reflected only in the initial HTML. If Grafana performs additional origin validation internally the dashboard may still fail to render; the reliable fix is to set Grafana's `root_url` to the URL actually used by the pod (see README Roadmap).
- **Row panels**: Grafana v5+ wraps panels inside `row` type panels. Always flatten the panel list before iterating (see `export_dashboard`).
- **Grid units**: Grafana's grid is 24 columns wide. The `w` field in `gridPos` is already in grid units. Do not multiply by anything for the CSS `grid-column: span <w>` value.
- **Base64 size**: a dashboard with many panels can produce a multi-megabyte HTML file. This is expected and acceptable.
- **Kubernetes Job re-runs**: the Job is one-shot. To export again, delete the Job (`kubectl delete job grafana-exporter`) and re-apply `k8s/job.yaml`. Do not use `kubectl rollout restart` — it does not apply to Jobs.
- **PVC access after Job**: use `kubectl cp` to pull the HTML files from the completed pod before deleting the Job or letting `ttlSecondsAfterFinished` clean it up.
