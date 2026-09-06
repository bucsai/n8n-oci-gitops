# n8n on OCI — GitOps Deployment

A GitOps-managed deployment of [n8n](https://n8n.io/) (workflow automation tool) on Oracle Cloud Infrastructure's Always Free tier, provisioned entirely through Infrastructure as Code and continuous deployment.

## Project Goals

- Provision a free-tier OCI Ampere (ARM) compute instance using Pulumi
- Deploy and run n8n on that instance via automated configuration management
- Expose n8n securely to the public internet through a Cloudflare Tunnel, mapped to a custom `.dev` domain — no open inbound ports on the VM
- Drive all provisioning and configuration changes through a GitHub Actions pipeline, so infrastructure changes are triggered by commits/PRs rather than manual `pulumi up` runs
- Keep all infrastructure state, configuration, and history versioned in this repository (GitOps model)

## Architecture (planned)

```
GitHub Actions (on push/PR)
        │
        ▼
   Pulumi (Python, uv toolchain)
        │
        ▼
  OCI Compartment
        │
        ▼
  VCN + Subnet + Security List
        │
        ▼
  Compute Instance (VM.Standard.A1.Flex, Always Free ARM shape)
        │
        ├── Docker + n8n (via cloud-init / config management)
        └── cloudflared (Cloudflare Tunnel client)
                │
                ▼
       Cloudflare Tunnel → custom .dev domain → public internet
```

## Tech Stack

| Layer | Choice |
|---|---|
| IaC | [Pulumi](https://www.pulumi.com/) (Python, `pulumi-oci` provider) |
| Package/venv management | [uv](https://docs.astral.sh/uv/) |
| Cloud provider | Oracle Cloud Infrastructure (OCI), Always Free tier |
| Compute | ARM Ampere `VM.Standard.A1.Flex` instance |
| Application | [n8n](https://n8n.io/) (self-hosted, Dockerized) |
| Ingress / exposure | Cloudflare Tunnel (`cloudflared`) |
| DNS | Custom `.dev` domain via Cloudflare |
| CI/CD | GitHub Actions |

## Why these choices

- **OCI Always Free ARM tier**: provides a genuinely free, always-on VM (up to 4 OCPUs / 24GB RAM across Ampere A1 instances), which is more generous than most free tiers and well suited to running a lightweight workflow engine like n8n.
- **Cloudflare Tunnel**: avoids exposing the VM's public IP or opening inbound firewall ports — the tunnel makes an outbound-only connection from the VM to Cloudflare's edge, which terminates TLS and routes traffic to the custom domain.
- **Pulumi over Terraform + Ansible**: the original plan was Terraform for provisioning paired with Ansible for configuration management. Given the scope of this project (a single VM running one application), that split added more moving parts than it was worth. Pulumi covers both provisioning and, via its Python program, enough configuration logic (e.g. cloud-init) to avoid needing a separate config management tool — one language, one tool, one state model.
- **GitHub Actions as the deployment trigger**: every infrastructure or configuration change goes through version control and a pipeline run, rather than being applied ad hoc from a local machine — the core GitOps principle this project demonstrates.

## Status

🚧 Early stage — repository currently contains Pulumi project scaffolding only. Compute, networking, n8n deployment, Cloudflare Tunnel integration, and the GitHub Actions pipeline are not yet implemented.

## Roadmap

- [ ] Define OCI networking (VCN, subnet, security list) via Pulumi
- [ ] Provision the ARM compute instance
- [ ] Automate Docker + n8n installation on the instance (cloud-init or dedicated config management)
- [ ] Set up Cloudflare Tunnel and DNS routing to the `.dev` domain
- [ ] Configure Pulumi remote state backend
- [ ] Build GitHub Actions workflow for `pulumi preview`/`pulumi up` on push/PR
- [ ] Document secrets/config management (OCI API keys, Cloudflare API token)

## Local Development

This project uses [uv](https://docs.astral.sh/uv/) as the Python toolchain for Pulumi.

```bash
# Install dependencies
uv sync

# Preview changes
pulumi preview

# Apply changes
pulumi up
```
