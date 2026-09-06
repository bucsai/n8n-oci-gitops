"""OCI networking + compute for n8n"""

import pulumi
import pulumi_oci as oci

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

internet_gateway = oci.core.InternetGateway(
    "n8n-igw",
    compartment_id=compartment.id,
    vcn_id=vcn.id,
    enabled=True,
    display_name="n8n-igw",
)

route_table = oci.core.RouteTable(
    "n8n-route-table",
    compartment_id=compartment.id,
    vcn_id=vcn.id,
    route_rules=[
        oci.core.RouteTableRouteRuleArgs(
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
            network_entity_id=internet_gateway.id,
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
)

pulumi.export("compartment_id", compartment.id)
pulumi.export("vcn_id", vcn.id)
pulumi.export("subnet_id", subnet.id)
