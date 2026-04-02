# AxonOps

**Neural Infrastructure Engine** — open-source AI that monitors, predicts, decides, and acts on your infrastructure automatically.

```
collect metrics → detect anomalies → decide action → gate confidence → execute → learn
```

---

## Quickstart — run in 60 seconds

**Prerequisites:** Docker + Docker Compose

```bash
git clone https://github.com/yourusername/axonops
cd axonops
cp .env.example .env
docker compose up
```

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3000 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

AxonOps starts monitoring the local machine immediately.

---

## What it does

Most tools alert you *after* something breaks. AxonOps runs a continuous reasoning loop:
collect → detect anomaly → select action → confidence gate → execute → learn.

**Three confidence tiers:**
- `≥ 0.85` — execute silently and log
- `0.60 – 0.85` — execute and notify your team
- `< 0.60` — pause and request human approval

Every decision and outcome is stored in memory. AxonOps gets smarter over time.

---

## Run without Docker

```bash
git clone https://github.com/yourusername/axonops && cd axonops
python -m venv .venv && source .venv/bin/activate

pip install -e ./axonops_sdk
pip install -e ./plugins/psutil ./plugins/docker ./plugins/prometheus
pip install -e ./axonops_core fastapi "uvicorn[standard]" websockets

axonops --tick 10 --log-level INFO
```

---

## Plugin system

Three plugin types. Install only what you need.

**Collector** — how metrics enter: `collect() → MetricBatch`
**Action** — what AxonOps can do: `execute(action) → ActionResult`
**Strategy** — the decision logic: `evaluate(metrics, context) → Decision`

### Official plugins

| Plugin | Install | Covers |
|--------|---------|--------|
| psutil | `pip install axonops-plugin-psutil` | CPU, memory, disk, processes |
| docker | `pip install axonops-plugin-docker` | Container stats, restart loops |
| prometheus | `pip install axonops-plugin-prometheus` | Any /metrics endpoint |
| kubernetes | `pip install axonops-plugin-kubernetes` | Pods, nodes, deployments |
| aws | `pip install axonops-plugin-aws` | EC2 ASG, RDS, CloudWatch |

### Writing a plugin

```python
from axonops_sdk import BaseCollector, MetricBatch, Metric
import socket

class MyCollector(BaseCollector):
    plugin_name = "my-service"
    plugin_version = "1.0.0"

    async def collect(self) -> MetricBatch:
        return MetricBatch(
            source=self.plugin_name, host=socket.gethostname(),
            metrics=[Metric(name="my_metric", value=42.0, unit="%")]
        )

    async def health_check(self) -> bool:
        return True
```

Register with entry points in `pyproject.toml`:

```toml
[project.entry-points."axonops.collectors"]
my-service = "my_package:MyCollector"
```

Install and it auto-discovers. No config needed.

---

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Engine status |
| GET | `/api/metrics` | Latest metric snapshot |
| GET | `/api/feed` | Decision history |
| GET | `/api/approvals` | Pending approvals |
| POST | `/api/approvals/{id}/approve` | Approve a decision |
| POST | `/api/approvals/{id}/reject` | Reject a decision |
| GET | `/api/plugins` | Loaded plugins |
| WS | `/ws` | Live stream |

---

## Project structure

```
axonops/
├── axonops_sdk/        SDK — models, base classes, registry
├── axonops_core/       Engine, FastAPI server, memory, CLI
├── plugins/
│   ├── psutil/         Local machine
│   ├── docker/         Containers
│   ├── prometheus/     Metrics scraping
│   ├── kubernetes/     K8s clusters
│   └── aws/            AWS infrastructure
├── ui/                 React dashboard (dark, minimal)
├── docker-compose.yml  One-command setup
├── Dockerfile
├── CONTRIBUTING.md
└── PLUGINS.md
```

---

## Configuration

Copy `.env.example` → `.env`:

```bash
AXONOPS_TICK=10
AXONOPS_CONFIDENCE_AUTO=0.85
AXONOPS_CONFIDENCE_NOTIFY=0.60
DATABASE_URL=                    # blank = SQLite dev mode
SLACK_WEBHOOK_URL=               # optional notifications
AWS_REGION=us-east-1
```

---

## License

[Business Source License 1.1](./LICENSE) — free for self-hosted use, commercial license for managed SaaS. Converts to Apache 2.0 in 2029.

Built by [Mohit Mahajan](https://linkedin.com/in/mohit-mahajan-052059206/).
