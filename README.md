# n8n on OCI — GitOps Deployment

A GitOps-managed deployment of [n8n](https://n8n.io/) (workflow automation tool) on Oracle Cloud Infrastructure's Always Free tier, provisioned entirely through Infrastructure as Code and continuous deployment.

## Project Goals

- Provision a free-tier OCI Ampere (ARM) compute instance using Pulumi
- Deploy and run n8n on that instance via cloud-init, treating the instance as immutable — no inbound access, no ad hoc changes; config updates mean replacing the instance, not logging into it
- Expose n8n securely to the public internet through a Cloudflare Tunnel, mapped to a custom `.dev` domain — no open inbound ports on the VM at all, including SSH
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
        ├── Docker (installed via cloud-init, one-shot at boot)
        │     ├── postgres  ─── data dir on attached Block Volume
        │     ├── n8n       ─── talks to postgres, ~/.n8n dir on Block Volume
        │     └── cloudflared (Cloudflare Tunnel client)
        │                         │
        │                         ▼
        │                Cloudflare Tunnel → n8n.bucsai.dev → public internet
        │
        └── Block Volume (separate lifecycle from the instance — survives
              instance replacement, so n8n's data outlives config changes)
```

## Tech Stack

| Layer | Choice |
|---|---|
| IaC | [Pulumi](https://www.pulumi.com/) (Python, `pulumi-oci` provider) |
| Package/venv management | [uv](https://docs.astral.sh/uv/) |
| Cloud provider | Oracle Cloud Infrastructure (OCI), Always Free tier |
| Compute | ARM Ampere `VM.Standard.A1.Flex` instance |
| Application | [n8n](https://n8n.io/) (self-hosted, Dockerized) |
| Database | PostgreSQL (Dockerized, data on a dedicated OCI Block Volume) |
| Ingress / exposure | Cloudflare Tunnel (`cloudflared`) |
| DNS | Custom `.dev` domain via Cloudflare |
| CI/CD | GitHub Actions |

## Why these choices

- **OCI Always Free ARM tier**: provides a genuinely free, always-on VM (up to 4 OCPUs / 24GB RAM across Ampere A1 instances), which is more generous than most free tiers and well suited to running a lightweight workflow engine like n8n.
- **Cloudflare Tunnel**: avoids exposing the VM's public IP or opening inbound firewall ports — the tunnel makes an outbound-only connection from the VM to Cloudflare's edge, which terminates TLS and routes traffic to the custom domain.
- **Pulumi over Terraform + Ansible**: the original plan was Terraform for provisioning paired with Ansible for configuration management. Given the scope of this project (a single VM running one application), that split added more moving parts than it was worth. Pulumi covers both provisioning and, via its Python program, enough configuration logic (e.g. cloud-init) to avoid needing a separate config management tool — one language, one tool, one state model.
- **Immutable instance, no SSH**: the instance has no inbound security list rules at all — not even SSH. All setup happens once via cloud-init at boot; a config change means replacing the instance through Pulumi, not connecting to fix it in place. This follows the "cattle, not pets" principle and keeps the actual running state from drifting away from what's declared in Git, which matters more once this is public-facing and accepting logins. Emergency debugging (if ever needed) goes through OCI's Instance Console Connection, which doesn't touch the VCN or require any open port.
- **GitHub Actions as the deployment trigger**: every infrastructure or configuration change goes through version control and a pipeline run, rather than being applied ad hoc from a local machine — the core GitOps principle this project demonstrates.
- **PostgreSQL on a dedicated Block Volume, not local SQLite**: n8n defaults to a local SQLite file for its workflows/credentials, which would be lost every time the instance is replaced. Postgres runs as its own container with its data directory on a separate OCI Block Volume — a resource with its own lifecycle, independent of the compute instance. Replacing the instance (a config change) never touches this volume, so the data survives. (OCI's free-tier "Autonomous Database" was considered first, but it's Oracle's own database engine, not Postgres-compatible, and isn't usable with n8n.) The volume is attached at instance *launch time* rather than as a separate post-launch step, so it's already present when cloud-init runs on first boot — avoiding a race between cloud-init and a later attachment call.

## Status

🚧 In progress — networking, compute, the Docker/Postgres/n8n stack, and Cloudflare Tunnel + DNS routing are provisioned via Pulumi. The GitHub Actions pipeline is not yet implemented.

## Roadmap

- [x] Define OCI networking (VCN, subnet, security list) via Pulumi
- [x] Provision the ARM compute instance
- [x] Automate Docker + n8n installation on the instance via cloud-init
- [x] Set up Cloudflare Tunnel and DNS routing to `n8n.bucsai.dev`
- [ ] Configure Pulumi remote state backend
- [ ] Build GitHub Actions workflow for `pulumi preview`/`pulumi up` on push/PR
- [ ] Document secrets/config management (OCI API keys, Cloudflare API token)

## Known Limitations / Follow-ups

- **`n8nio/n8n:latest` is unpinned.** Every instance replacement pulls whatever is current at that moment, which is non-reproducible and can introduce breaking changes silently. Should be pinned to a specific version tag once the stack is stable enough that upgrades can be deliberate, versioned changes in `__main__.py` instead.
- **All three container images (`postgres`, `n8n`, `cloudflared`) are pulled fresh on every instance replacement.** Only `/mnt/n8n-data` (Postgres data, n8n's `~/.n8n`) survives on the persistent Block Volume — the boot disk, and with it Docker's image cache, is thrown away with the old instance. Expect every config change / redeploy to take a few minutes for image pulls before n8n is reachable again, worse on Always Free's throttled NAT egress. Pinning versions (above) would at least make pulls reproducible, not faster.
- **The documented "emergency debugging via Instance Console Connection" doesn't work as described.** A Console Connection only authenticates the tunnel to OCI's serial console proxy — the `<instance> login:` prompt still needs a real OS credential, and none is configured (no SSH key, no password), by design. Console history capture is also not useful for this: Ubuntu's ARM cloud image doesn't mirror cloud-init/docker output to the serial console, so the captured buffer only has early kernel boot messages. In practice, diagnosing a boot-time failure currently means temporarily adding a `chpasswd` block to `CLOUD_INIT_TEMPLATE` in `__main__.py`, redeploying, and removing it again afterward — not a real fix, just what worked when n8n's container hit a bind-mount permission error (see the `chown` step in `runcmd`) and needed live `docker compose logs` to diagnose.
- **Instance sized at 2 OCPU / 12GB**, half of the Always Free ARM allowance (4 OCPU / 24GB total) — deliberate headroom, not a constraint. Adjustable via `shape_config` in `__main__.py` if n8n needs more.

## Prerequisites

Before running anything in this repo, the following need to be in place:

- **OCI account** with the Always Free tier available in your home region (Ampere A1 capacity isn't guaranteed in every region — worth checking availability first).
- **OCI API key configured for Pulumi**, either as a local `~/.oci/config` file or as equivalent Pulumi OCI provider config values. You'll need, from the OCI Console (Identity → Users → your user → API Keys):
  - Tenancy OCID
  - User OCID
  - Region
  - An API signing key pair, with the public key uploaded to your OCI user and the fingerprint noted
- **Pulumi CLI** installed, and a [Pulumi Cloud](https://app.pulumi.com/) account for the state backend (free for individual use). Run `pulumi login` once locally.
- **Two application secrets** set via Pulumi config before applying the stack:
  ```bash
  pulumi config set --secret postgresPassword "$(openssl rand -hex 24)"
  pulumi config set --secret n8nEncryptionKey "$(openssl rand -hex 32)"
  ```
  `n8nEncryptionKey` in particular must stay fixed across instance replacements — n8n uses it to encrypt stored credentials, so changing it makes existing credentials unreadable.
- **uv** installed for Python dependency/toolchain management (`uv sync` sets up the venv used by Pulumi).
- **Cloudflare account** with `bucsai.dev` added as a zone, plus a Cloudflare API token scoped for `Account.Cloudflare Tunnel:Edit` and `Zone.DNS:Edit` on that zone. Set the following Pulumi config before applying the stack:
  ```bash
  pulumi config set --secret cloudflareApiToken "<token>"
  pulumi config set --secret cloudflareAccountId "<account id, from the Cloudflare dashboard URL or API>"
  pulumi config set --secret cloudflareZoneId "<zone id for bucsai.dev, from the domain overview page>"
  ```
  `n8nHostname` defaults to `n8n.bucsai.dev`; override with `pulumi config set n8nHostname <hostname>` if needed.
- **GitHub repository secrets**, once the Actions pipeline is added:
  - `PULUMI_ACCESS_TOKEN` — lets CI authenticate to the Pulumi Cloud backend
  - OCI credentials (API key contents, tenancy/user OCIDs, fingerprint, region) — passed as Pulumi config or provider env vars
  - Cloudflare API token — for Tunnel/DNS provisioning

Nothing above should ever be committed in plaintext; anything stack-specific (like the OCI credentials above) goes in Pulumi config via `pulumi config set --secret <key> <value>`, which encrypts the value before it's written to `Pulumi.<stack>.yaml`.

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
