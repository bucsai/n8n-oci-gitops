"""OCI networking + compute for n8n"""

import base64

import pulumi
import pulumi_cloudflare as cloudflare
import pulumi_oci as oci

config = pulumi.Config()
postgres_password = config.require_secret("postgresPassword")
n8n_encryption_key = config.require_secret("n8nEncryptionKey")
cloudflare_api_token = config.require_secret("cloudflareApiToken")
cloudflare_account_id = config.require_secret("cloudflareAccountId")
cloudflare_zone_id = config.require_secret("cloudflareZoneId")
n8n_hostname = config.get("n8nHostname") or "n8n.bucsai.dev"

DATA_DEVICE = "/dev/oracleoci/oraclevdb"
DATA_MOUNT_POINT = "/mnt/n8n-data"

CLOUD_INIT_TEMPLATE = """#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-v2

write_files:
  - path: /opt/n8n/.env
    permissions: "0600"
    content: |
      POSTGRES_PASSWORD={postgres_password}
      N8N_ENCRYPTION_KEY={n8n_encryption_key}
      TUNNEL_TOKEN={tunnel_token}

  - path: /opt/n8n/docker-compose.yml
    permissions: "0644"
    content: |
      services:
        postgres:
          image: postgres:16-alpine
          restart: unless-stopped
          environment:
            POSTGRES_DB: n8n
            POSTGRES_USER: n8n
            POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD}}
          volumes:
            - {data_mount}/postgres:/var/lib/postgresql/data
          healthcheck:
            test: ["CMD-SHELL", "pg_isready -U n8n"]
            interval: 5s
            timeout: 5s
            retries: 10

        n8n:
          image: n8nio/n8n:latest
          restart: unless-stopped
          depends_on:
            postgres:
              condition: service_healthy
          environment:
            DB_TYPE: postgresdb
            DB_POSTGRESDB_HOST: postgres
            DB_POSTGRESDB_PORT: "5432"
            DB_POSTGRESDB_DATABASE: n8n
            DB_POSTGRESDB_USER: n8n
            DB_POSTGRESDB_PASSWORD: ${{POSTGRES_PASSWORD}}
            N8N_ENCRYPTION_KEY: ${{N8N_ENCRYPTION_KEY}}
            N8N_HOST: {hostname}
            N8N_PROTOCOL: https
            N8N_PORT: "5678"
            WEBHOOK_URL: https://{hostname}/
          volumes:
            - {data_mount}/n8n:/home/node/.n8n

        cloudflared:
          image: cloudflare/cloudflared:latest
          restart: unless-stopped
          command: tunnel run
          environment:
            TUNNEL_TOKEN: ${{TUNNEL_TOKEN}}
          depends_on:
            - n8n
      # No ports are published on n8n or postgres: n8n is reached only
      # through the cloudflared container, which makes an outbound-only
      # connection to Cloudflare's edge and proxies to n8n over this compose
      # file's default internal network.

runcmd:
  # Format the data volume only if it has no filesystem yet, so re-running
  # this on an existing (already-formatted) volume never wipes its data.
  # This matters because the instance is replaced on config changes while
  # the volume persists independently across those replacements.
  - [ mkdir, -p, "{data_mount}" ]
  - bash -c 'blkid {device} || mkfs.ext4 -F {device}'
  - bash -c 'UUID=$(blkid -s UUID -o value {device}); grep -q "$UUID" /etc/fstab || echo "UUID=$UUID {data_mount} ext4 defaults,nofail 0 2" >> /etc/fstab'
  - mount -a
  - [ mkdir, -p, "{data_mount}/postgres", "{data_mount}/n8n" ]
  # n8nio/n8n runs as uid 1000 (the "node" user) and needs to write into its
  # bind-mounted ~/.n8n dir; mkdir above leaves it root-owned, which n8n
  # can't write to (postgres's entrypoint fixes its own dir's ownership,
  # n8n's doesn't).
  - [ chown, -R, "1000:1000", "{data_mount}/n8n" ]
  - systemctl enable --now docker
  - bash -c 'cd /opt/n8n && docker compose --env-file .env up -d'
"""


def _render_cloud_init(
    pg_password: str, encryption_key: str, tunnel_token: str, hostname: str
) -> str:
    rendered = CLOUD_INIT_TEMPLATE.format(
        postgres_password=pg_password,
        n8n_encryption_key=encryption_key,
        tunnel_token=tunnel_token,
        hostname=hostname,
        data_mount=DATA_MOUNT_POINT,
        device=DATA_DEVICE,
    )
    return base64.b64encode(rendered.encode()).decode()


cloudflare_provider = cloudflare.Provider(
    "cloudflare",
    api_token=cloudflare_api_token,
)

# Config is managed remotely (config_src="cloudflare") via the Config
# resource below, rather than shipped as a local config file on the VM —
# keeps the tunnel's routing declared here in Git, not on the instance.
tunnel = cloudflare.ZeroTrustTunnelCloudflared(
    "n8n-tunnel",
    account_id=cloudflare_account_id,
    name="n8n-tunnel",
    config_src="cloudflare",
    opts=pulumi.ResourceOptions(provider=cloudflare_provider),
)

tunnel_config = cloudflare.ZeroTrustTunnelCloudflaredConfig(
    "n8n-tunnel-config",
    account_id=cloudflare_account_id,
    tunnel_id=tunnel.id,
    config=cloudflare.ZeroTrustTunnelCloudflaredConfigConfigArgs(
        ingresses=[
            cloudflare.ZeroTrustTunnelCloudflaredConfigConfigIngressArgs(
                hostname=n8n_hostname,
                service="http://n8n:5678",
            ),
            # Catch-all rule required by cloudflared for any request that
            # doesn't match a hostname above.
            cloudflare.ZeroTrustTunnelCloudflaredConfigConfigIngressArgs(
                service="http_status:404",
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(provider=cloudflare_provider),
)

dns_record = cloudflare.DnsRecord(
    "n8n-dns-record",
    zone_id=cloudflare_zone_id,
    name=n8n_hostname,
    type="CNAME",
    content=tunnel.id.apply(lambda tunnel_id: f"{tunnel_id}.cfargotunnel.com"),
    ttl=1,  # required "automatic" TTL when proxied
    proxied=True,
    opts=pulumi.ResourceOptions(provider=cloudflare_provider),
)

tunnel_token = cloudflare.get_zero_trust_tunnel_cloudflared_token_output(
    account_id=cloudflare_account_id,
    tunnel_id=tunnel.id,
    opts=pulumi.InvokeOutputOptions(provider=cloudflare_provider),
).token

compartment = oci.identity.Compartment(
    "n8n-compartment",
    name="n8n-compartment",
    description="Resources for the n8n GitOps project",
    enable_delete=True,
)

vcn = oci.core.Vcn(
    "n8n-vcn",
    compartment_id=compartment.id,
    cidr_blocks=["10.0.0.0/16"],
    display_name="n8n-vcn",
    dns_label="n8nvcn",
)

# No Internet Gateway: nothing in this VCN ever gets a public IP, so there's
# no public-facing entity that would need one. Outbound-only internet access
# (for Docker image pulls, apt/dnf, and the Cloudflare Tunnel connection) goes
# through a NAT Gateway instead.
nat_gateway = oci.core.NatGateway(
    "n8n-nat-gateway",
    compartment_id=compartment.id,
    vcn_id=vcn.id,
    display_name="n8n-nat-gateway",
)

route_table = oci.core.RouteTable(
    "n8n-route-table",
    compartment_id=compartment.id,
    vcn_id=vcn.id,
    route_rules=[
        oci.core.RouteTableRouteRuleArgs(
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
            network_entity_id=nat_gateway.id,
        )
    ],
)

security_list = oci.core.SecurityList(
    "n8n-security-list",
    compartment_id=compartment.id,
    vcn_id=vcn.id,
    egress_security_rules=[
        oci.core.SecurityListEgressSecurityRuleArgs(
            destination="0.0.0.0/0",
            protocol="all",
        )
    ],
    # No ingress rules: no inbound access at all. n8n is reached only via
    # Cloudflare Tunnel (outbound-only), and the instance is managed as
    # immutable (cloud-init at boot, replaced rather than SSH'd into for
    # changes). Emergency debugging goes through OCI's Instance Console
    # Connection, which bypasses the VCN entirely.
    ingress_security_rules=[],
)

subnet = oci.core.Subnet(
    "n8n-subnet",
    compartment_id=compartment.id,
    vcn_id=vcn.id,
    cidr_block="10.0.1.0/24",
    display_name="n8n-subnet",
    dns_label="n8nsubnet",
    route_table_id=route_table.id,
    security_list_ids=[security_list.id],
    # Private subnet: no VNIC created here can ever be assigned a public IP,
    # regardless of what's requested at instance launch time.
    prohibit_public_ip_on_vnic=True,
    opts=pulumi.ResourceOptions(delete_before_replace=True),
)

availability_domain = oci.identity.get_availability_domains_output(
    compartment_id=compartment.id
).availability_domains[0].name

ubuntu_arm_image_id = oci.core.get_images_output(
    compartment_id=compartment.id,
    operating_system="Canonical Ubuntu",
    operating_system_version="24.04",
    shape="VM.Standard.A1.Flex",
    sort_by="TIMECREATED",
    sort_order="DESC",
).images[0].id

# Independent lifecycle from the instance: this volume is never touched by
# instance replacements, so n8n's Postgres data and file storage survive
# config changes that recreate the compute instance.
data_volume = oci.core.Volume(
    "n8n-data-volume",
    compartment_id=compartment.id,
    availability_domain=availability_domain,
    display_name="n8n-data-volume",
    size_in_gbs="50",
)

cloud_init_b64 = pulumi.Output.all(
    postgres_password, n8n_encryption_key, tunnel_token, n8n_hostname
).apply(lambda args: _render_cloud_init(*args))

instance = oci.core.Instance(
    "n8n-instance",
    compartment_id=compartment.id,
    availability_domain=availability_domain,
    shape="VM.Standard.A1.Flex",
    shape_config=oci.core.InstanceShapeConfigArgs(
        ocpus=2,
        memory_in_gbs=12,
    ),
    source_details=oci.core.InstanceSourceDetailsArgs(
        source_type="image",
        source_id=ubuntu_arm_image_id,
    ),
    create_vnic_details=oci.core.InstanceCreateVnicDetailsArgs(
        subnet_id=subnet.id,
        assign_public_ip="false",
    ),
    # Attached at launch (not as a separate post-launch attachment) so the
    # volume is already present when cloud-init runs on first boot.
    launch_volume_attachments=[
        oci.core.InstanceLaunchVolumeAttachmentArgs(
            type="paravirtualized",
            volume_id=data_volume.id,
            device=DATA_DEVICE,
        )
    ],
    metadata={
        "user_data": cloud_init_b64,
    },
    display_name="n8n-instance",
    # The data volume can only be attached to one instance at a time, so a
    # config-driven replace must detach it (by deleting the old instance)
    # before the new one can attach it at launch — the default
    # create-before-delete ordering fails with a 409 Conflict.
    opts=pulumi.ResourceOptions(delete_before_replace=True),
)

pulumi.export("compartment_id", compartment.id)
pulumi.export("vcn_id", vcn.id)
pulumi.export("subnet_id", subnet.id)
pulumi.export("instance_id", instance.id)
pulumi.export("data_volume_id", data_volume.id)
pulumi.export("tunnel_id", tunnel.id)
pulumi.export("n8n_url", pulumi.Output.concat("https://", n8n_hostname))
