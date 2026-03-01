"""SendGrid email service configuration for service integration."""

from app.agents.service_configs import ServiceConfig, register_service

SENDGRID = ServiceConfig(
    name="sendgrid",
    display_name="SendGrid",
    category="email",
    supported_languages=("python", "typescript"),
    package_dependencies={
        "python": ("sendgrid",),
        "typescript": ("@sendgrid/mail",),
    },
    environment_variables=(
        "SENDGRID_API_KEY",
        "SENDGRID_FROM_EMAIL",
        "SENDGRID_FROM_NAME",
    ),
    rules=(
        "ALWAYS use dynamic transactional templates (template_id) for all "
        "user-facing emails instead of inline HTML; templates are managed in "
        "the SendGrid dashboard and allow non-engineer edits without deploys.",

        "ALWAYS set a verified sender identity (from_email) that matches a "
        "domain authenticated in your SendGrid account; emails from unverified "
        "senders are silently dropped or flagged as spam.",

        "NEVER send emails synchronously in request handlers; enqueue email "
        "sends to a background task queue (Celery, BullMQ) so API latency is "
        "not affected by SendGrid response times or rate limits.",

        "ALWAYS include both text/plain and text/html content in every email "
        "to ensure readability in all clients; the text version is used by "
        "screen readers and plain-text email clients.",

        "ALWAYS set reply_to to a monitored mailbox address that differs from "
        "the no-reply from address so users can respond to transactional "
        "emails when they need support.",

        "ALWAYS implement rate limiting on email-triggering endpoints to "
        "prevent abuse; SendGrid enforces daily send limits and exceeding them "
        "results in hard blocks on your account.",

        "ALWAYS use categories and custom_args on every email for analytics "
        "tracking and filtering in the SendGrid Activity Feed; include at "
        "minimum the email type (e.g. 'welcome', 'invoice', 'reset').",

        "ALWAYS validate recipient email addresses with a basic format check "
        "before calling the SendGrid API; invalid addresses count against your "
        "bounce rate and damage sender reputation.",

        "ALWAYS configure Event Webhook to receive delivery, bounce, and spam "
        "report events; use these to update your suppression list and stop "
        "sending to addresses that hard-bounce.",

        "NEVER hard-code the API key in source code; load it from environment "
        "variables and use scoped API keys with only the permissions needed "
        "(Mail Send, Template Engine) instead of full-access keys.",
    ),
    golden_examples={
        "send_email": (
            "import os\n"
            "from sendgrid import SendGridAPIClient\n"
            "from sendgrid.helpers.mail import Mail, Email, To, Content\n"
            "\n"
            "sg = SendGridAPIClient(os.environ[\"SENDGRID_API_KEY\"])\n"
            "\n"
            "\n"
            "def send_email(\n"
            "    to_email: str,\n"
            "    subject: str,\n"
            "    html_content: str,\n"
            "    text_content: str,\n"
            ") -> int:\n"
            "    \"\"\"Send a transactional email and return the status code.\"\"\"\n"
            "    message = Mail(\n"
            "        from_email=Email(os.environ[\"SENDGRID_FROM_EMAIL\"],\n"
            "                         os.environ[\"SENDGRID_FROM_NAME\"]),\n"
            "        to_emails=To(to_email),\n"
            "        subject=subject,\n"
            "    )\n"
            "    message.content = [\n"
            "        Content(\"text/plain\", text_content),\n"
            "        Content(\"text/html\", html_content),\n"
            "    ]\n"
            "    message.reply_to = Email(\"support@example.com\")\n"
            "    message.category = [\"transactional\"]\n"
            "    response = sg.send(message)\n"
            "    return response.status_code"
        ),
        "template_email": (
            "import os\n"
            "from sendgrid import SendGridAPIClient\n"
            "from sendgrid.helpers.mail import Mail\n"
            "\n"
            "sg = SendGridAPIClient(os.environ[\"SENDGRID_API_KEY\"])\n"
            "\n"
            "\n"
            "def send_template_email(\n"
            "    to_email: str,\n"
            "    template_id: str,\n"
            "    dynamic_data: dict,\n"
            ") -> int:\n"
            "    \"\"\"Send an email using a SendGrid dynamic template.\"\"\"\n"
            "    message = Mail(\n"
            "        from_email=os.environ[\"SENDGRID_FROM_EMAIL\"],\n"
            "        to_emails=to_email,\n"
            "    )\n"
            "    message.template_id = template_id\n"
            "    message.dynamic_template_data = dynamic_data\n"
            "    message.category = [dynamic_data.get(\"email_type\", \"general\")]\n"
            "    response = sg.send(message)\n"
            "    return response.status_code"
        ),
        "bulk_email": (
            "import sgMail from \"@sendgrid/mail\";\n"
            "\n"
            "sgMail.setApiKey(process.env.SENDGRID_API_KEY!);\n"
            "\n"
            "interface Recipient {\n"
            "  email: string;\n"
            "  name: string;\n"
            "  data: Record<string, unknown>;\n"
            "}\n"
            "\n"
            "export async function sendBulkTemplateEmail(\n"
            "  recipients: Recipient[],\n"
            "  templateId: string,\n"
            "): Promise<void> {\n"
            "  const messages = recipients.map((r) => ({\n"
            "    to: { email: r.email, name: r.name },\n"
            "    from: {\n"
            "      email: process.env.SENDGRID_FROM_EMAIL!,\n"
            "      name: process.env.SENDGRID_FROM_NAME!,\n"
            "    },\n"
            "    templateId,\n"
            "    dynamicTemplateData: r.data,\n"
            "    category: [\"bulk\"],\n"
            "  }));\n"
            "\n"
            "  // SendGrid allows up to 1000 messages per batch\n"
            "  const batchSize = 1000;\n"
            "  for (let i = 0; i < messages.length; i += batchSize) {\n"
            "    await sgMail.send(messages.slice(i, i + batchSize));\n"
            "  }\n"
            "}"
        ),
    },
    setup_code={
        "python": (
            "import os\n"
            "from sendgrid import SendGridAPIClient\n"
            "\n"
            "sg_client = SendGridAPIClient(os.environ[\"SENDGRID_API_KEY\"])"
        ),
        "typescript": (
            "import sgMail from \"@sendgrid/mail\";\n"
            "\n"
            "sgMail.setApiKey(process.env.SENDGRID_API_KEY!);\n"
            "\n"
            "export default sgMail;"
        ),
    },
)

register_service(SENDGRID)
