# Nexus Image Builder Agent

This app deploys a suspended `CronJob` template that uses Kaniko to build and push images to:

- `docker.michaelhomelab.work/homelab-docker-repo/...`

The job trusts Nexus TLS by mounting `ca.crt` from a cert issued by `vault-issuer`.

## Required Secret

Create Docker registry credentials in `devops-tools` namespace:

```bash
kubectl -n devops-tools create secret docker-registry nexus-docker-config \
  --docker-server=docker.michaelhomelab.work \
  --docker-username='<username>' \
  --docker-password='<password>' \
  --docker-email='devnull@michaelhomelab.work'
```

## Dynamic Build Inputs

Edit config values in `nexus-image-builder-config`:

- `GIT_CONTEXT` (Kaniko context: git URL or remote tar)
- `DOCKERFILE` (path inside context)
- `DESTINATION_IMAGE` (full image URL)

## Private GitHub Org Wiring

For public repos, the default `GIT_CONTEXT` in the ConfigMap is enough.

For private org repos, create an override secret in `devops-tools` with a PAT-backed context URL:

```bash
kubectl -n devops-tools create secret generic nexus-image-builder-git-auth \
  --from-literal=GIT_CONTEXT='https://x-access-token:<GITHUB_PAT>@github.com/pradeepmichaelwork/homelab-k8s-platform.git#refs/heads/main'
```

Notes:

- Use a fine-grained PAT with repository `Contents: Read`.
- If your org enforces SSO, authorize the PAT for the org.
- The CronJob is already wired with `envFrom.secretRef(optional: true)`, so this secret automatically overrides the public `GIT_CONTEXT`.

## Trigger Build

Run on demand from the suspended cron template:

```bash
kubectl -n devops-tools create job --from=cronjob/nexus-image-builder nexus-image-builder-manual-$(date +%s)
```
