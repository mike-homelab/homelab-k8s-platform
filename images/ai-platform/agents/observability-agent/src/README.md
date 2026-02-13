# Observability Agent

Read-only observability agent for the homelab AI platform.

## Responsibilities (planned)
- Query Prometheus / Mimir
- Query Loki logs
- Query Tempo traces
- Read Kubernetes state
- Explain incidents and anomalies

## Non-goals
- No mutation
- No kubectl writes
- No secret management

## Endpoints
- `/health` – liveness
- `/ready` – readiness

## Runtime
- Port: 8080
- Runs as non-root
