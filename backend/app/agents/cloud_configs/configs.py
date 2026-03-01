"""Cloud platform configurations for all 10 supported hosting providers.

Each config is registered at module load time via ``register_cloud()``.
Platforms cover: serverless, container orchestration, PaaS, and static hosting.
"""

from app.agents.cloud_configs import CloudConfig, register_cloud

# ---------------------------------------------------------------------------
# 1. Vercel -- serverless
# ---------------------------------------------------------------------------

VERCEL = CloudConfig(
    name="vercel",
    display_name="Vercel",
    category="serverless",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "nuxt", "remix", "solidstart",
    ),
    config_files={
        "vercel.json": (
            '{\n'
            '  "buildCommand": "npm run build",\n'
            '  "outputDirectory": ".next",\n'
            '  "framework": "nextjs",\n'
            '  "regions": ["iad1"],\n'
            '  "env": {\n'
            '    "NODE_ENV": "production"\n'
            '  }\n'
            '}'
        ),
    },
    environment_variables=(
        "VERCEL_TOKEN",
        "VERCEL_ORG_ID",
        "VERCEL_PROJECT_ID",
    ),
    deploy_command="vercel deploy --prod",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=True,
    rules=(
        "Use Vercel Serverless Functions for API routes; keep each function under "
        "the 50 MB size limit and 10-second default timeout for the hobby plan.",
        "Enable ISR (Incremental Static Regeneration) for pages that change "
        "infrequently to reduce serverless invocations and improve TTFB.",
        "Set environment variables through the Vercel dashboard or CLI rather than "
        "committing .env files to version control.",
        "Configure redirects and rewrites in vercel.json instead of application-level "
        "middleware to leverage edge-level routing for lower latency.",
        "Use Vercel Analytics and Speed Insights to monitor Core Web Vitals in "
        "production; set performance budgets for LCP under 2.5 seconds.",
    ),
)

# ---------------------------------------------------------------------------
# 2. AWS ECS -- container
# ---------------------------------------------------------------------------

AWS_ECS = CloudConfig(
    name="aws_ecs",
    display_name="AWS ECS",
    category="container",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "Dockerfile": (
            "FROM node:20-alpine AS builder\n"
            "WORKDIR /app\n"
            "COPY package*.json ./\n"
            "RUN npm ci --production\n"
            "COPY . .\n"
            "RUN npm run build\n"
            "EXPOSE 3000\n"
            'CMD ["node", "server.js"]'
        ),
        "task-definition.json": (
            '{\n'
            '  "family": "app-task",\n'
            '  "cpu": "256",\n'
            '  "memory": "512",\n'
            '  "networkMode": "awsvpc",\n'
            '  "containerDefinitions": [{\n'
            '    "name": "app",\n'
            '    "image": "${ECR_IMAGE}",\n'
            '    "portMappings": [{"containerPort": 3000}]\n'
            '  }]\n'
            '}'
        ),
    },
    environment_variables=(
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "ECR_REPOSITORY_URI",
    ),
    deploy_command="aws ecs update-service --cluster prod --service app --force-new-deployment",
    supports_preview=False,
    supports_custom_domain=True,
    free_tier=False,
    rules=(
        "Use Fargate launch type for serverless container execution to avoid managing "
        "EC2 instances; switch to EC2 launch type only for GPU or cost-sensitive workloads.",
        "Store container images in ECR with immutable tags and enable image scanning "
        "to detect vulnerabilities before deployment.",
        "Configure Application Load Balancer health checks with a dedicated /health "
        "endpoint that verifies database connectivity and returns 200.",
        "Set CPU and memory limits in the task definition to prevent noisy-neighbour "
        "issues; use 256 CPU / 512 MB as the baseline for lightweight services.",
        "Enable ECS Service Auto Scaling with target tracking on CPU utilization "
        "at 70% and set minimum/maximum task counts to handle traffic spikes.",
    ),
)

# ---------------------------------------------------------------------------
# 3. GCP Cloud Run -- container
# ---------------------------------------------------------------------------

GCP_CLOUD_RUN = CloudConfig(
    name="gcp_cloud_run",
    display_name="GCP Cloud Run",
    category="container",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "cloudbuild.yaml": (
            "steps:\n"
            "  - name: 'gcr.io/cloud-builders/docker'\n"
            "    args: ['build', '-t', 'gcr.io/$PROJECT_ID/app', '.']\n"
            "  - name: 'gcr.io/cloud-builders/docker'\n"
            "    args: ['push', 'gcr.io/$PROJECT_ID/app']\n"
            "  - name: 'gcr.io/cloud-builders/gcloud'\n"
            "    args: ['run', 'deploy', 'app',\n"
            "           '--image', 'gcr.io/$PROJECT_ID/app',\n"
            "           '--region', 'us-central1']"
        ),
        "service.yaml": (
            "apiVersion: serving.knative.dev/v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - image: gcr.io/PROJECT_ID/app\n"
            "          ports:\n"
            "            - containerPort: 8080"
        ),
    },
    environment_variables=(
        "GCP_PROJECT_ID",
        "GCP_REGION",
        "GCP_SERVICE_ACCOUNT_KEY",
    ),
    deploy_command="gcloud run deploy app --source . --region us-central1",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=True,
    rules=(
        "Set the concurrency limit per container instance (default 80) based on your "
        "application's thread safety; single-threaded apps should use concurrency=1.",
        "Configure minimum instances to 1 for latency-sensitive services to eliminate "
        "cold-start delays; use 0 for cost-optimized batch workloads.",
        "Use Cloud Run revisions and traffic splitting for canary deployments; route "
        "5-10% of traffic to the new revision before promoting to 100%.",
        "Connect to Cloud SQL via the built-in Unix socket proxy instead of public IP "
        "to keep database traffic on Google's internal network.",
        "Set memory limits to at least 512 MiB for Node.js services and 256 MiB for "
        "Go services; Cloud Run terminates containers that exceed their memory limit.",
    ),
)

# ---------------------------------------------------------------------------
# 4. Railway -- paas
# ---------------------------------------------------------------------------

RAILWAY = CloudConfig(
    name="railway",
    display_name="Railway",
    category="paas",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "railway.toml": (
            "[build]\n"
            'builder = "nixpacks"\n'
            'buildCommand = "npm run build"\n'
            "\n"
            "[deploy]\n"
            'startCommand = "npm start"\n'
            "healthcheckPath = \"/health\"\n"
            "restartPolicyType = \"on_failure\""
        ),
    },
    environment_variables=(
        "RAILWAY_TOKEN",
        "DATABASE_URL",
        "PORT",
    ),
    deploy_command="railway up --detach",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=True,
    rules=(
        "Use Railway's built-in Nixpacks builder for automatic detection of runtime "
        "and dependencies; override only when custom build steps are required.",
        "Provision databases (Postgres, Redis, MySQL) as Railway services in the same "
        "project to use private networking and avoid egress charges.",
        "Set a health check path in railway.toml so Railway can perform zero-downtime "
        "deploys by waiting for the new instance to become healthy.",
        "Use Railway's preview environments linked to pull requests for staging; each "
        "PR gets an isolated environment with its own database.",
        "Monitor resource usage via Railway's metrics dashboard and set spend alerts "
        "to avoid unexpected charges on the usage-based billing model.",
    ),
)

# ---------------------------------------------------------------------------
# 5. Azure App Service -- container
# ---------------------------------------------------------------------------

AZURE_APP_SERVICE = CloudConfig(
    name="azure_app_service",
    display_name="Azure App Service",
    category="container",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "azure-pipelines.yml": (
            "trigger:\n"
            "  - main\n"
            "pool:\n"
            "  vmImage: 'ubuntu-latest'\n"
            "steps:\n"
            "  - task: Docker@2\n"
            "    inputs:\n"
            "      command: buildAndPush\n"
            "      repository: $(containerRegistry)/app\n"
            "      dockerfile: Dockerfile\n"
            "  - task: AzureWebAppContainer@1\n"
            "    inputs:\n"
            "      appName: $(webAppName)"
        ),
    },
    environment_variables=(
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
        "AZURE_APP_NAME",
        "AZURE_CREDENTIALS",
    ),
    deploy_command="az webapp up --name myapp --resource-group mygroup --runtime 'NODE:20-lts'",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=False,
    rules=(
        "Use deployment slots (staging, production) for zero-downtime deployments; "
        "swap slots after verifying the staging slot passes health checks.",
        "Enable Application Insights for distributed tracing, live metrics, and "
        "automatic anomaly detection across your App Service instances.",
        "Configure autoscale rules based on CPU percentage (scale out at 70%) and "
        "HTTP queue length to handle traffic spikes without over-provisioning.",
        "Use Azure Key Vault references in application settings instead of storing "
        "secrets directly in environment variables or configuration files.",
        "Set WEBSITES_ENABLE_APP_SERVICE_STORAGE to false for containerized apps "
        "to avoid mounting the shared storage volume and improve startup time.",
    ),
)

# ---------------------------------------------------------------------------
# 6. DigitalOcean App Platform -- paas
# ---------------------------------------------------------------------------

DIGITALOCEAN = CloudConfig(
    name="digitalocean",
    display_name="DigitalOcean App Platform",
    category="paas",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "app.yaml": (
            "name: my-app\n"
            "services:\n"
            "  - name: web\n"
            "    github:\n"
            "      repo: user/repo\n"
            "      branch: main\n"
            "    build_command: npm run build\n"
            "    run_command: npm start\n"
            "    http_port: 3000\n"
            "    instance_count: 1\n"
            "    instance_size_slug: basic-xxs"
        ),
    },
    environment_variables=(
        "DIGITALOCEAN_ACCESS_TOKEN",
        "DATABASE_URL",
        "PORT",
    ),
    deploy_command="doctl apps create --spec app.yaml",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=False,
    rules=(
        "Define all services, databases, and workers in a single app.yaml spec file "
        "for reproducible deployments across environments.",
        "Use DigitalOcean Managed Databases as app components to get automatic backups, "
        "failover, and private networking between services.",
        "Configure health checks with a custom HTTP path and set the failure threshold "
        "to 3 to avoid restarting containers during brief transient errors.",
        "Use the built-in container registry (DOCR) for private Docker images to keep "
        "image pulls fast and within the DigitalOcean network.",
        "Set instance_size_slug appropriately: basic-xxs (512 MB) for staging and "
        "professional-xs (1 GB) or higher for production workloads.",
    ),
)

# ---------------------------------------------------------------------------
# 7. Netlify -- static/serverless
# ---------------------------------------------------------------------------

NETLIFY = CloudConfig(
    name="netlify",
    display_name="Netlify",
    category="serverless",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "nuxt", "remix", "gatsby", "hugo", "eleventy",
    ),
    config_files={
        "netlify.toml": (
            "[build]\n"
            '  command = "npm run build"\n'
            '  publish = "dist"\n'
            '  functions = "netlify/functions"\n'
            "\n"
            "[[redirects]]\n"
            '  from = "/api/*"\n'
            '  to = "/.netlify/functions/:splat"\n'
            "  status = 200"
        ),
    },
    environment_variables=(
        "NETLIFY_AUTH_TOKEN",
        "NETLIFY_SITE_ID",
    ),
    deploy_command="netlify deploy --prod",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=True,
    rules=(
        "Use Netlify Functions (AWS Lambda under the hood) for server-side logic; keep "
        "each function under 50 MB bundled and 10-second execution for the free tier.",
        "Configure _redirects or netlify.toml redirects for SPA routing to avoid 404s "
        "on direct navigation to client-side routes.",
        "Enable Netlify's built-in form handling for simple contact forms instead of "
        "spinning up a separate backend or third-party service.",
        "Use branch deploys and deploy previews on pull requests to test changes in "
        "isolated environments before merging to the production branch.",
        "Set immutable caching headers for hashed static assets and use Netlify's "
        "built-in CDN invalidation on each deploy for zero-downtime updates.",
    ),
)

# ---------------------------------------------------------------------------
# 8. Fly.io -- container
# ---------------------------------------------------------------------------

FLY_IO = CloudConfig(
    name="fly_io",
    display_name="Fly.io",
    category="container",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "fly.toml": (
            'app = "my-app"\n'
            'primary_region = "iad"\n'
            "\n"
            "[build]\n"
            '  dockerfile = "Dockerfile"\n'
            "\n"
            "[http_service]\n"
            "  internal_port = 8080\n"
            "  force_https = true\n"
            "\n"
            "[[services.tcp_checks]]\n"
            "  interval = \"10s\"\n"
            "  timeout = \"2s\""
        ),
    },
    environment_variables=(
        "FLY_API_TOKEN",
        "DATABASE_URL",
        "PRIMARY_REGION",
    ),
    deploy_command="fly deploy",
    supports_preview=False,
    supports_custom_domain=True,
    free_tier=False,
    rules=(
        "Use Fly Machines (V2) for per-request scaling; Machines start in under 300ms "
        "and can scale to zero when idle to minimize costs.",
        "Deploy to multiple regions using fly.toml regions list and use Fly's Anycast "
        "IP to route users to the nearest instance automatically.",
        "Store persistent data in Fly Volumes or use an external managed database; "
        "Fly Machines are ephemeral and data is lost on redeployment.",
        "Use fly secrets set for environment variables so they are encrypted at rest "
        "and injected at runtime without appearing in the fly.toml file.",
        "Configure health checks with both TCP and HTTP checks in fly.toml to ensure "
        "Fly's proxy only routes traffic to healthy instances.",
    ),
)

# ---------------------------------------------------------------------------
# 9. Render -- paas
# ---------------------------------------------------------------------------

RENDER = CloudConfig(
    name="render",
    display_name="Render",
    category="paas",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "render.yaml": (
            "services:\n"
            "  - type: web\n"
            "    name: app\n"
            "    runtime: node\n"
            "    buildCommand: npm install && npm run build\n"
            "    startCommand: npm start\n"
            "    envVars:\n"
            "      - key: NODE_ENV\n"
            "        value: production\n"
            "    healthCheckPath: /health"
        ),
    },
    environment_variables=(
        "RENDER_API_KEY",
        "DATABASE_URL",
        "PORT",
    ),
    deploy_command="render deploy",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=True,
    rules=(
        "Use render.yaml (Infrastructure as Code) to define all services, databases, "
        "and cron jobs in a single file for reproducible deployments.",
        "Provision Render PostgreSQL or Redis instances within the same region as your "
        "web service to minimize latency on internal network requests.",
        "Configure a health check path so Render performs zero-downtime deploys by "
        "waiting for the new instance to respond before cutting over traffic.",
        "Use Render's preview environments linked to pull requests for isolated "
        "testing with their own databases and environment variables.",
        "Set auto-scaling rules with a minimum of 1 instance for production and "
        "allow Render to scale up based on CPU and memory thresholds.",
    ),
)

# ---------------------------------------------------------------------------
# 10. Heroku -- paas
# ---------------------------------------------------------------------------

HEROKU = CloudConfig(
    name="heroku",
    display_name="Heroku",
    category="paas",
    supported_frameworks=(
        "nextjs", "react", "vue", "svelte", "astro",
        "fastapi", "django", "express", "rails", "spring",
        "flask", "nestjs", "laravel", "gin", "phoenix",
    ),
    config_files={
        "Procfile": (
            "web: npm start\n"
            "worker: node worker.js\n"
            "release: npm run migrate"
        ),
        "app.json": (
            '{\n'
            '  "name": "my-app",\n'
            '  "stack": "heroku-24",\n'
            '  "buildpacks": [\n'
            '    {"url": "heroku/nodejs"}\n'
            '  ],\n'
            '  "env": {\n'
            '    "NODE_ENV": {"value": "production"}\n'
            '  }\n'
            '}'
        ),
    },
    environment_variables=(
        "HEROKU_API_KEY",
        "DATABASE_URL",
        "PORT",
    ),
    deploy_command="git push heroku main",
    supports_preview=True,
    supports_custom_domain=True,
    free_tier=False,
    rules=(
        "Define a Procfile with explicit process types (web, worker, release) so "
        "Heroku knows how to start each dyno type in your application.",
        "Use Heroku Postgres add-on with connection pooling enabled; set the "
        "DATABASE_URL config var and keep connections under the plan limit.",
        "Run database migrations in the release phase (release: in Procfile) so "
        "they execute once before the new version receives traffic.",
        "Enable Heroku's review apps in app.json for automatic preview environments "
        "on pull requests with their own ephemeral databases.",
        "Set WEB_CONCURRENCY based on dyno size (2 for Standard-1X, 4 for "
        "Standard-2X) to fully utilize available memory without swapping.",
    ),
)

# -- Register all configs -----------------------------------------------------

register_cloud(VERCEL)
register_cloud(AWS_ECS)
register_cloud(GCP_CLOUD_RUN)
register_cloud(RAILWAY)
register_cloud(AZURE_APP_SERVICE)
register_cloud(DIGITALOCEAN)
register_cloud(NETLIFY)
register_cloud(FLY_IO)
register_cloud(RENDER)
register_cloud(HEROKU)
