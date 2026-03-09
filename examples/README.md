# SelfHealing Examples

Reference configuration examples for consumer apps integrating the selfhealing library.

## Quick Start

### 1. Install selfhealing

```bash
pip install selfhealing
# or add to pyproject.toml dependencies
```

### 2. Django Configuration

Copy and adapt [django-setup/](django-setup/) files to your project:

- **settings.py** — Add `selfhealing.adapters.django` to `INSTALLED_APPS`,
  call `configure_selfhealing(namespace=globals())` at the end.
- **celery.py** — Use `configure_selfhealing_celery(app)` for Beat/Queue/Route
  registration, and `setup_selfhealing_signals(app=app)` for signal hooks.
- **gunicorn.conf.py** — Wire fork/init/exit hooks via `selfhealing.server` helpers.
- **urls.py** — Include `selfhealing.api.django.urls` for the Self-Healing API.

### 3. Infrastructure Sandbox (Phase 1)

Spin up the minimum infrastructure for selfhealing with a single command:

```bash
cd examples/docker
docker-compose up -d
```

This starts Redis, PostgreSQL, OTEL Collector, Tempo, Mimir, Loki, and Grafana.
Your app is **not** included — connect from your own project:

```bash
# .env
DATABASE_URL=postgres://selfhealing_user:selfhealing_pass@localhost:5432/selfhealing_db
REDIS_URL=redis://localhost:6379/0
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_TRACES_SAMPLER=always_on
```

Open Grafana at `http://localhost:3000` (admin/admin) to see metrics, traces, and logs.

### 4. Kubernetes

See [k8s/](k8s/) for Kustomize base + overlay manifests.

```bash
# Preview generated manifests
kubectl kustomize examples/k8s/overlays/production

# Apply
kubectl apply -k examples/k8s/overlays/production
```

### 5. Monitoring

- **prometheus-alerts.yml** — Alert rules for DLQ, Circuit Breaker, SLO, Latency.
- **prometheus-adapter.yaml** — Custom metrics adapter config for HPA.
- **grafana-dashboard.json** — Portable dashboard with `__inputs` for DS mapping.
- **otel-collector.yml** — Generalized collector with Tail Sampling.

### 6. Dashboard Portability

Sanitize exported Grafana dashboards before sharing:

```bash
bash examples/scripts/sanitize-dashboards.sh examples/monitoring
```

## Directory Structure

```
examples/
├── README.md
├── django-setup/
│   ├── settings.py
│   ├── celery.py
│   ├── gunicorn.conf.py
│   └── urls.py
├── k8s/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── deployment.yaml
│   │   ├── celery-worker.yaml
│   │   ├── celery-critical-worker.yaml
│   │   ├── hpa.yaml
│   │   ├── keda-scaledobject.yaml
│   │   ├── pdb.yaml
│   │   ├── servicemonitor.yaml
│   │   └── networkpolicy.yaml
│   └── overlays/
│       ├── dev/
│       ├── staging/
│       └── production/
├── monitoring/
│   ├── prometheus-alerts.yml
│   ├── prometheus-adapter.yaml
│   ├── grafana-dashboard.json
│   └── otel-collector.yml
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── scripts/
    └── sanitize-dashboards.sh
```

## Phase 2 (future)

A follow-up document will add an end-to-end runnable demo under `examples/sandbox/`
with a dummy consumer app and scenario scripts (CB demo, DLQ replay, error budget, chaos).
