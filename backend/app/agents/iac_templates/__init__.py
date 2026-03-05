"""Infrastructure-as-Code templates for Terraform HCL generation.

D5-FIX: Generates Terraform HCL files alongside existing cloud configs.
Each cloud provider has a Terraform module template that provisions
the required infrastructure (compute, database, networking, secrets).

Usage:
    from app.agents.iac_templates.terraform import get_terraform_templates

    templates = get_terraform_templates("gcp_cloud_run")
    if templates:
        for filename, hcl_content in templates.items():
            ...
"""
