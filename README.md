# grafana-exporter

Export Grafana dashboards as **self-contained offline HTML files** — no internet connection required to view them.

Paste a dashboard URL (or use `--all`), run the script, copy the resulting `.html` file to a USB stick, and open it on any PC with a browser.

---

## How it works

The script calls the Grafana HTTP API to discover dashboards and their panel layout, then renders every panel as an image. All images are base64-encoded and embedded directly in a single HTML file styled to match Grafana's dark theme.

Two rendering backends are supported and tried automatically in order:

| Priority | Backend | How | Quality |
|---|---|---|---|
| 1 | **Grafana Image Renderer** plugin | `/render/d-solo/` API — panel by panel | Best — preserves grid layout |
| 2 | **Playwright** headless Chromium | Full-page screenshot fallback | Good — whole page as one image |

If neither is available the script exits with a clear error explaining what to install.

---

## Requirements

- Python 3.9+
- Network access to your Grafana instance at export time
- At least one rendering backend (see below)

---

## Installation

### Linux (Ubuntu, Debian, Mint, Fedora, RHEL, Arch, …)

**1. Install Python** (if not already present):

```bash
# Debian / Ubuntu / Mint
sudo apt-get install python3 python3-venv

# Fedora / RHEL / CentOS
sudo dnf install python3
```

**2. Clone the repo and run the setup script:**

```bash
git clone <this-repo>
cd grafana-exporter
./setup.sh
source .venv/bin/activate
```

`setup.sh` creates a Python virtual environment, installs Playwright, and then runs `playwright install-deps chromium` which automatically detects your distribution and installs the required system libraries using the correct package manager (`apt`, `dnf`, `pacman`, etc.).

If `install-deps` needs root you will be prompted for your password.

### macOS

**1. Install Python** (if not already present):

```bash
brew install python
```

**2. Clone the repo and run the setup script:**

```bash
git clone <this-repo>
cd grafana-exporter
./setup.sh
source .venv/bin/activate
```

### Manual install (any platform)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright
playwright install-deps chromium   # Linux only — installs system libs
playwright install chromium
```

### Alternative: Grafana Image Renderer plugin (no Python deps needed)

If you control the Grafana server, installing the Image Renderer plugin is the better option — it renders each panel individually at full quality and requires no extra setup on the exporter side:

```bash
# On the Grafana server
grafana-cli plugins install grafana-image-renderer
systemctl restart grafana-server
```

The script detects the plugin automatically and uses it instead of Playwright.

---

## Usage

```
python3 grafana_export.py [OPTIONS] [DASHBOARD_URL …]
```

### Authentication

Credentials are passed to Playwright via its `http_credentials` context option, **never embedded in the URL**. Embedding `user:password@host` in the URL causes Grafana's frontend JavaScript to fail with a browser security error (`fetch` refuses URLs that contain credentials).

Use a **service account token** (recommended) or basic auth:

```bash
# Service account token
export GRAFANA_TOKEN=glsa_xxxxxxxxxxxx

# Or basic auth
export GRAFANA_USER=admin
export GRAFANA_PASSWORD=secret
```

Environment variables are read automatically. You can also pass them as flags (`--token`, `--user`, `--password`).

> **API token + Playwright**: a service account token cannot be injected into a browser session. If your Grafana has anonymous access disabled and you pass only `--token`, the Playwright path will land on the login page. Use `--user`/`--password` for the Playwright backend.

### Examples

```bash
# Export one dashboard — time range is read from the URL automatically
python3 grafana_export.py \
  --user admin --password secret \
  "http://localhost:3000/d/grad-test-matrix/gradient-test?from=now-5m&to=now"

# Export several dashboards at once
python3 grafana_export.py \
  --user admin --password secret \
  "http://localhost:3000/d/uid1/dashboard-one" \
  "http://localhost:3000/d/uid2/dashboard-two"

# Export ALL dashboards found in Grafana
python3 grafana_export.py --all --user admin --password secret

# Use a service account token (works with Image Renderer backend)
python3 grafana_export.py --token glsa_YOUR_TOKEN \
  "http://localhost:3000/d/grad-test-matrix/gradient-test?from=now-5m&to=now"

# Override the time range regardless of what is in the URL
python3 grafana_export.py --from now-1h --to now \
  --user admin --password secret \
  "http://localhost:3000/d/grad-test-matrix/gradient-test"

# Custom output directory
python3 grafana_export.py --out ./exports \
  --user admin --password secret \
  "http://localhost:3000/d/grad-test-matrix/gradient-test"

# Force a specific rendering backend
python3 grafana_export.py --backend playwright \
  --user admin --password secret \
  "http://localhost:3000/d/grad-test-matrix/gradient-test"

# Custom output filename (single dashboard only; .html added automatically)
python3 grafana_export.py --name my-export \
  --user admin --password secret \
  "http://localhost:3000/d/grad-test-matrix/gradient-test?from=now-5m&to=now"
```

### All options

| Flag | Default | Description |
|---|---|---|
| `DASHBOARD_URL …` | — | One or more full Grafana dashboard URLs |
| `--all` | off | Export every dashboard in the Grafana instance |
| `--base-url` | `http://localhost:3000` | Grafana base URL (used with `--all`) |
| `--token` | `$GRAFANA_TOKEN` | Service account / API token |
| `--user` | `$GRAFANA_USER` / `admin` | Username for basic auth |
| `--password` | `$GRAFANA_PASSWORD` / `admin` | Password for basic auth |
| `--from` | from URL | Override time range start (e.g. `now-1h`) |
| `--to` | from URL | Override time range end (e.g. `now`) |
| `--out` | `./grafana-export` | Output directory |
| `--backend` | `auto` | `auto` \| `renderer` \| `playwright` |
| `--width` | `1920` | Viewport width for Playwright screenshots |
| `--name` | — | Output filename (without path). Only valid for a single dashboard. `.html` is appended automatically if omitted. |

---

## Output

Each dashboard is saved as `<Dashboard Title>.html` inside the output directory. Unicode dashes in titles are normalised to `-`; other special characters become `_`. Use `--name` to override the filename for a single-dashboard export. The file is fully self-contained — open it in any modern browser, no server or internet required.

```
grafana-export/
  Gradient_Test-Full_Matrix.html   # default: derived from dashboard title
  my-export.html                   # with --name my-export
  My_Other_Dashboard.html
```

---

## Docker

Build and run locally with Docker:

```bash
# Build the image
docker build -t grafana-exporter:latest .

# Export a specific dashboard (output lands in ./grafana-export/)
docker run --rm \
  -v "$(pwd)/grafana-export:/output" \
  grafana-exporter:latest \
  --user admin --password secret \
  "http://host.docker.internal:3000/d/grad-test-matrix/gradient-test?from=now-1h&to=now"

# Export ALL dashboards
docker run --rm \
  -v "$(pwd)/grafana-export:/output" \
  grafana-exporter:latest \
  --all --user admin --password secret \
  --base-url http://host.docker.internal:3000
```

> On Linux replace `host.docker.internal` with the host IP (e.g. `172.17.0.1`) or use `--network host`.

---

## Kubernetes

The `k8s/` directory contains manifests to run the exporter as a one-shot **Job** on any Kubernetes cluster.

### Files

| File | Purpose |
|---|---|
| `k8s/secret.yaml` | Grafana credentials (username/password or token) |
| `k8s/configmap.yaml` | Grafana base URL, dashboard URLs, time range |
| `k8s/pvc.yaml` | PersistentVolumeClaim for the HTML output |
| `k8s/job.yaml` | The export Job |
| `k8s/kustomization.yaml` | Applies all of the above with a single command |

### Quick start

**1. Build and push the image to your registry:**

```bash
docker build -t your-registry/grafana-exporter:latest .
docker push your-registry/grafana-exporter:latest
```

**2. Edit the manifests:**

- `k8s/secret.yaml` — set your Grafana username and password
- `k8s/configmap.yaml` — set `GRAFANA_BASE_URL` to the in-cluster Grafana service DNS name and update `DASHBOARD_URLS` with the dashboards you want to export
- `k8s/job.yaml` — update `image:` to point to your registry

**3. Deploy:**

```bash
kubectl apply -k k8s/
```

**4. Watch progress:**

```bash
kubectl logs -f job/grafana-exporter
```

**5. Copy the HTML files to your local machine:**

```bash
POD=$(kubectl get pods -l job-name=grafana-exporter -o jsonpath='{.items[0].metadata.name}')
kubectl cp ${POD}:/output ./grafana-export
```

**6. Clean up:**

```bash
kubectl delete -k k8s/
```

### Exporting all dashboards

In `k8s/configmap.yaml` set:

```yaml
EXPORT_ALL: "true"
```

The Job will use `--all` and discover every dashboard from Grafana automatically.

### Re-running the export

The Job is a one-shot run. To run again:

```bash
kubectl delete job grafana-exporter
kubectl apply -f k8s/job.yaml
```

### Grafana in the same cluster

If Grafana is running in the same cluster the base URL should use its Kubernetes service DNS name, for example:

```
http://grafana.monitoring.svc.cluster.local:3000
```

Update `GRAFANA_BASE_URL` in `k8s/configmap.yaml` accordingly.

### Known limitation — Grafana `root_url` mismatch in Kubernetes

When Grafana runs **outside** the cluster (e.g. on the host machine) and is reached from within a pod via a host IP such as `10.89.0.1:3000`, Grafana's `root_url` may still be set to `http://localhost:3000`. The script patches the HTML document to rewrite these references before Chromium parses them, but some internal Grafana boot configuration can still cause the React app to fail initialising.

**Workaround**: set Grafana's `root_url` to the URL the pod actually uses to reach it:

```ini
# grafana.ini
[server]
root_url = http://10.89.0.1:3000
```

This issue does **not** occur when Grafana runs inside the same cluster and is accessed via its Kubernetes service DNS name (e.g. `http://grafana.monitoring.svc.cluster.local:3000`), because the URL Playwright navigates to already matches `root_url`.

---

## Roadmap

Items not yet implemented. Contributions welcome.

- [ ] **K8s: full support for out-of-cluster Grafana** — reliably handle the case where Grafana's `root_url` (`localhost`) differs from the IP used inside the pod; likely requires a sidecar TCP proxy or an init-container that rewrites `/etc/hosts`
- [ ] **PDF export** — `playwright`'s `page.pdf()` instead of `page.screenshot()`, preserving vector text
- [ ] **PNG export per panel** — write raw panel PNGs to disk instead of embedding them in HTML (useful for slide decks)
- [ ] **CronJob manifest** — `k8s/cronjob.yaml` for scheduled recurring exports
- [ ] **Output upload** — push exported files to S3, GCS, or an HTTP endpoint after export
- [ ] **Grafana Image Renderer auto-install** — detect when the plugin is absent and print a one-line install command for the running Grafana version
- [ ] **Multi-org support** — iterate over all organisations, not just `orgId=1`
- [ ] **Variable/template override** — pass Grafana template variable values via CLI flags so the same dashboard can be exported with different filters

---

## Creating a Grafana service account token

1. In Grafana go to **Administration → Service accounts → Add service account**
2. Set role to **Viewer**
3. Click **Add service account token**, copy the token
4. Pass it via `--token` or the `GRAFANA_TOKEN` environment variable (or `token` key in `k8s/secret.yaml`)
