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
    display_name="n8n-instance",
)

pulumi.export("compartment_id", compartment.id)
pulumi.export("vcn_id", vcn.id)
pulumi.export("subnet_id", subnet.id)
pulumi.export("instance_id", instance.id)
