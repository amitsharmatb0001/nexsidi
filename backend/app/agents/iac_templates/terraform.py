"""Terraform HCL templates for all supported cloud providers.

D5-FIX: Each template is a dict mapping filename to HCL content.
Templates use Python ``str.format_map()`` placeholders: ``{service_name}``,
``{region}``, ``{project_id}``, etc.

Terraform braces (``{{ }}``) are escaped as ``{{{{ }}}}`` to survive
Python's format_map.  Shell-style ``${{var.xxx}}`` is pre-escaped to
``${{{{var.xxx}}}}`` in the template strings.

Templates are minimal and production-ready:
- Provider configuration with required_version
- Compute resource (Cloud Run, ECS Fargate, App Service, etc.)
- Networking where applicable
- Outputs (URL, service ID, etc.)
- Variables for user-configurable values
"""

from __future__ import annotations

from typing import Any

# Provider name -> {filename -> HCL content}
TERRAFORM_TEMPLATES: dict[str, dict[str, str]] = {}


# ── GCP Cloud Run ──────────────────────────────────────────────────

TERRAFORM_TEMPLATES["gcp_cloud_run"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    google = {{{{
      source  = "hashicorp/google"
      version = "~> 5.0"
    }}}}
  }}}}
  backend "gcs" {{{{
    bucket = "{project_id}-tfstate"
    prefix = "terraform/state"
  }}}}
}}}}

provider "google" {{{{
  project = "{project_id}"
  region  = "{region}"
}}}}

resource "google_cloud_run_v2_service" "{service_name}" {{{{
  name     = "{service_name}"
  location = "{region}"

  template {{{{
    containers {{{{
      image = var.container_image
      resources {{{{
        limits = {{{{
          cpu    = "{cpu}"
          memory = "{memory}"
        }}}}
      }}}}
      ports {{{{
        container_port = 8000
      }}}}
      liveness_probe {{{{
        http_get {{{{
          path = "{health_check_path}"
        }}}}
      }}}}
    }}}}
    scaling {{{{
      min_instance_count = {min_instances}
      max_instance_count = {max_instances}
    }}}}
  }}}}
}}}}

resource "google_cloud_run_v2_service_iam_member" "public" {{{{
  project  = google_cloud_run_v2_service.{service_name}.project
  location = google_cloud_run_v2_service.{service_name}.location
  name     = google_cloud_run_v2_service.{service_name}.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}}}}

output "service_url" {{{{
  value = google_cloud_run_v2_service.{service_name}.uri
}}}}
""",
    "variables.tf": """\
variable "container_image" {{{{
  description = "Container image to deploy (e.g. gcr.io/project/image:tag)"
  type        = string
}}}}
""",
}


# ── AWS ECS Fargate ────────────────────────────────────────────────

TERRAFORM_TEMPLATES["aws_ecs"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    aws = {{{{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}}}
  }}}}
}}}}

provider "aws" {{{{
  region = "{region}"
}}}}

resource "aws_ecs_cluster" "{service_name}" {{{{
  name = "{service_name}"
}}}}

resource "aws_ecs_task_definition" "{service_name}" {{{{
  family                   = "{service_name}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 1024
  memory                   = 2048

  container_definitions = jsonencode([{{{{
    name      = "{service_name}"
    image     = var.container_image
    essential = true
    portMappings = [{{{{
      containerPort = 8000
      hostPort      = 8000
    }}}}]
    healthCheck = {{{{
      command = ["CMD-SHELL", "curl -f http://localhost:8000{health_check_path} || exit 1"]
    }}}}
  }}}}])
}}}}

resource "aws_ecs_service" "{service_name}" {{{{
  name            = "{service_name}"
  cluster         = aws_ecs_cluster.{service_name}.id
  task_definition = aws_ecs_task_definition.{service_name}.arn
  desired_count   = {min_instances}
  launch_type     = "FARGATE"

  network_configuration {{{{
    subnets          = var.subnets
    security_groups  = var.security_groups
    assign_public_ip = true
  }}}}
}}}}

output "cluster_arn" {{{{
  value = aws_ecs_cluster.{service_name}.arn
}}}}

output "service_name" {{{{
  value = aws_ecs_service.{service_name}.name
}}}}
""",
    "variables.tf": """\
variable "container_image" {{{{
  description = "Container image URI (e.g. 123456789.dkr.ecr.us-east-1.amazonaws.com/app:latest)"
  type        = string
}}}}

variable "subnets" {{{{
  description = "List of subnet IDs for ECS tasks"
  type        = list(string)
}}}}

variable "security_groups" {{{{
  description = "List of security group IDs for ECS tasks"
  type        = list(string)
  default     = []
}}}}
""",
}


# ── Azure App Service ─────────────────────────────────────────────

TERRAFORM_TEMPLATES["azure_app_service"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    azurerm = {{{{
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }}}}
  }}}}
}}}}

provider "azurerm" {{{{
  features {{{{}}}}
}}}}

resource "azurerm_resource_group" "{service_name}" {{{{
  name     = "rg-{service_name}"
  location = "{region}"
}}}}

resource "azurerm_service_plan" "{service_name}" {{{{
  name                = "asp-{service_name}"
  resource_group_name = azurerm_resource_group.{service_name}.name
  location            = azurerm_resource_group.{service_name}.location
  os_type             = "Linux"
  sku_name            = "B1"
}}}}

resource "azurerm_linux_web_app" "{service_name}" {{{{
  name                = "{service_name}"
  resource_group_name = azurerm_resource_group.{service_name}.name
  location            = azurerm_resource_group.{service_name}.location
  service_plan_id     = azurerm_service_plan.{service_name}.id

  site_config {{{{
    application_stack {{{{
      docker_image_name = var.container_image
    }}}}
    health_check_path = "{health_check_path}"
  }}}}
}}}}

output "app_url" {{{{
  value = "https://${{azurerm_linux_web_app.{service_name}.default_hostname}}"
}}}}
""",
    "variables.tf": """\
variable "container_image" {{{{
  description = "Docker image (e.g. myregistry.azurecr.io/app:latest)"
  type        = string
}}}}
""",
}


# ── Fly.io ─────────────────────────────────────────────────────────

TERRAFORM_TEMPLATES["fly_io"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    fly = {{{{
      source  = "fly-apps/fly"
      version = "~> 0.1"
    }}}}
  }}}}
}}}}

provider "fly" {{{{
  # FLY_API_TOKEN env var must be set
}}}}

resource "fly_app" "{service_name}" {{{{
  name = "{service_name}"
  org  = var.fly_org
}}}}

resource "fly_machine" "{service_name}" {{{{
  app    = fly_app.{service_name}.name
  region = "{region}"
  name   = "{service_name}-web"

  image = var.container_image

  cpus     = {cpu}
  memorymb = 512

  services = [
    {{{{
      ports = [
        {{{{
          port     = 443
          handlers = ["tls", "http"]
        }}}},
        {{{{
          port     = 80
          handlers = ["http"]
        }}}}
      ]
      protocol      = "tcp"
      internal_port = 8000
    }}}}
  ]
}}}}

output "app_url" {{{{
  value = "https://${{fly_app.{service_name}.name}}.fly.dev"
}}}}
""",
    "variables.tf": """\
variable "container_image" {{{{
  description = "Docker image to deploy"
  type        = string
}}}}

variable "fly_org" {{{{
  description = "Fly.io organization slug"
  type        = string
  default     = "personal"
}}}}
""",
}


# ── DigitalOcean App Platform ──────────────────────────────────────

TERRAFORM_TEMPLATES["digitalocean"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    digitalocean = {{{{
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }}}}
  }}}}
}}}}

provider "digitalocean" {{{{
  # DIGITALOCEAN_TOKEN env var must be set
}}}}

resource "digitalocean_app" "{service_name}" {{{{
  spec {{{{
    name   = "{service_name}"
    region = "{region}"

    service {{{{
      name               = "{service_name}-web"
      instance_count     = {min_instances}
      instance_size_slug = "basic-xxs"

      image {{{{
        registry_type = "DOCKER_HUB"
        registry      = var.docker_registry
        repository    = var.docker_repository
        tag           = var.docker_tag
      }}}}

      http_port = 8000

      health_check {{{{
        http_path = "{health_check_path}"
      }}}}
    }}}}
  }}}}
}}}}

output "app_url" {{{{
  value = digitalocean_app.{service_name}.live_url
}}}}
""",
    "variables.tf": """\
variable "docker_registry" {{{{
  description = "Docker registry (e.g. your-dockerhub-username)"
  type        = string
}}}}

variable "docker_repository" {{{{
  description = "Docker repository name"
  type        = string
}}}}

variable "docker_tag" {{{{
  description = "Docker image tag"
  type        = string
  default     = "latest"
}}}}
""",
}


# ── Railway ────────────────────────────────────────────────────────
# Railway is a PaaS without an official Terraform provider.
# We provide documentation-only Terraform that describes the intended
# infrastructure for migration to other providers.

TERRAFORM_TEMPLATES["railway"] = {
    "main.tf": """\
# Railway is a PaaS — infrastructure is fully managed by Railway.
# This Terraform file documents the intended infrastructure for
# potential migration to other providers via Terraform.
#
# For Railway-native configuration, use railway.json / railway.toml.
#
# Infrastructure specification:
#   Service: {service_name}
#   Region:  {region}
#   Memory:  {memory}
#   CPU:     {cpu}
#   Health:  {health_check_path}
#   Min instances: {min_instances}
#   Max instances: {max_instances}
#
# To deploy on Railway:
#   railway login
#   railway link
#   railway up

terraform {{{{
  required_version = ">= 1.5"
}}}}

# No resources — Railway handles infrastructure automatically.
# This file serves as infrastructure documentation only.
""",
}


# ── Vercel ─────────────────────────────────────────────────────────
# Vercel is primarily a frontend PaaS. The Terraform provider is
# community-maintained.

TERRAFORM_TEMPLATES["vercel"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    vercel = {{{{
      source  = "vercel/vercel"
      version = "~> 1.0"
    }}}}
  }}}}
}}}}

provider "vercel" {{{{
  # VERCEL_API_TOKEN env var must be set
}}}}

resource "vercel_project" "{service_name}" {{{{
  name      = "{service_name}"
  framework = "nextjs"

  git_repository = {{{{
    type = "github"
    repo = var.github_repo
  }}}}
}}}}

output "project_id" {{{{
  value = vercel_project.{service_name}.id
}}}}
""",
    "variables.tf": """\
variable "github_repo" {{{{
  description = "GitHub repository (org/repo format)"
  type        = string
}}}}
""",
}


# ── Render ─────────────────────────────────────────────────────────

TERRAFORM_TEMPLATES["render"] = {
    "main.tf": """\
# Render does not have an official Terraform provider.
# This file documents the intended infrastructure for migration.
#
# Infrastructure specification:
#   Service: {service_name}
#   Region:  {region}
#   Memory:  {memory}
#   Health:  {health_check_path}
#
# To deploy on Render:
#   Create a Web Service at https://dashboard.render.com
#   Connect your GitHub/GitLab repository
#   Render auto-deploys on push to main branch

terraform {{{{
  required_version = ">= 1.5"
}}}}

# No resources — use render.yaml for Render-native configuration.
""",
}


# ── Heroku ─────────────────────────────────────────────────────────

TERRAFORM_TEMPLATES["heroku"] = {
    "main.tf": """\
terraform {{{{
  required_version = ">= 1.5"
  required_providers {{{{
    heroku = {{{{
      source  = "heroku/heroku"
      version = "~> 5.0"
    }}}}
  }}}}
}}}}

provider "heroku" {{{{
  # HEROKU_API_KEY and HEROKU_EMAIL env vars must be set
}}}}

resource "heroku_app" "{service_name}" {{{{
  name   = "{service_name}"
  region = "{region}"
  stack  = "container"
}}}}

resource "heroku_formation" "web" {{{{
  app_id   = heroku_app.{service_name}.id
  type     = "web"
  quantity = {min_instances}
  size     = "basic"
}}}}

output "app_url" {{{{
  value = heroku_app.{service_name}.web_url
}}}}
""",
    "variables.tf": """\
# Heroku uses Procfile + heroku.yml for configuration.
# No additional Terraform variables needed for basic setup.
""",
}


# ── Netlify ────────────────────────────────────────────────────────

TERRAFORM_TEMPLATES["netlify"] = {
    "main.tf": """\
# Netlify is a frontend-focused PaaS (JAMstack).
# Infrastructure specification:
#   Site: {service_name}
#   Region: {region}
#
# For Netlify-native configuration, use netlify.toml.
# Deploy via: netlify deploy --prod

terraform {{{{
  required_version = ">= 1.5"
}}}}

# No resources — use netlify.toml for Netlify-native configuration.
""",
}


# ── Public API ─────────────────────────────────────────────────────


def get_terraform_templates(provider_name: str) -> dict[str, str] | None:
    """Get Terraform HCL templates for a cloud provider.

    Args:
        provider_name: Canonical CloudConfig name (e.g., ``"gcp_cloud_run"``).

    Returns:
        Dict mapping filename (e.g., ``"main.tf"``) to HCL content,
        or ``None`` if no templates exist for the provider.
    """
    return TERRAFORM_TEMPLATES.get(provider_name)


def list_providers_with_terraform() -> list[str]:
    """Return list of provider names that have Terraform templates."""
    return list(TERRAFORM_TEMPLATES.keys())
