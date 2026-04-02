# AxonOps Plugin Registry

## Official plugins (shipped with v1)

| Plugin | Install | Covers |
|--------|---------|--------|
| axonops-plugin-psutil | `pip install axonops-plugin-psutil` | Local machine — CPU, memory, disk, processes |
| axonops-plugin-docker | `pip install axonops-plugin-docker` | Docker containers — stats, restart loops, scaling |
| axonops-plugin-prometheus | `pip install axonops-plugin-prometheus` | Any Prometheus /metrics endpoint |
| axonops-plugin-kubernetes | `pip install axonops-plugin-kubernetes` | Kubernetes pods, nodes, deployments |
| axonops-plugin-aws | `pip install axonops-plugin-aws` | AWS — EC2 ASG, RDS, CloudWatch |

## Community plugins

*None yet — be the first!*

To list your plugin here, open a PR that adds a row to this table.
Your plugin must implement at least one of `BaseCollector`, `BaseAction`, or `BaseStrategy`
from `axonops-sdk`, have a public GitHub repo, and include a README and tests.

## Writing a plugin

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full guide.
The three interfaces are documented in [axonops_sdk/axonops_sdk/base.py](./axonops_sdk/axonops_sdk/base.py).
