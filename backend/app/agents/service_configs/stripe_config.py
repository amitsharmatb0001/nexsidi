"""Stripe payment processing configuration for service integration."""

from app.agents.service_configs import ServiceConfig, register_service

STRIPE = ServiceConfig(
    name="stripe",
    display_name="Stripe",
    category="payment",
    supported_languages=("python", "typescript"),
    package_dependencies={
        "python": ("stripe",),
        "typescript": ("stripe",),
    },
    environment_variables=(
        "STRIPE_SECRET_KEY",
        "STRIPE_PUBLISHABLE_KEY",
        "STRIPE_WEBHOOK_SECRET",
    ),
    rules=(
        "ALWAYS verify webhook signatures using stripe.Webhook.construct_event() "
        "(Python) or stripe.webhooks.constructEvent() (TypeScript) with the raw "
        "request body; NEVER parse the JSON before verification as this defeats "
        "signature validation.",

        "ALWAYS use idempotency keys (Idempotency-Key header) for all mutating "
        "API calls (charges, refunds, transfers) to prevent duplicate operations "
        "when retrying failed requests.",

        "NEVER log or store full card numbers, CVV, or raw payment method details "
        "in application code, logs, or databases; use Stripe tokens (tok_*) and "
        "PaymentMethod IDs (pm_*) exclusively to maintain PCI compliance.",

        "ALWAYS use Stripe Checkout Sessions or Payment Intents for payment flows "
        "instead of direct Charge creation; the Charge API is legacy and lacks "
        "built-in SCA/3D Secure support required in the EU.",

        "ALWAYS handle all relevant Stripe error types explicitly: "
        "CardError (decline), RateLimitError (backoff), InvalidRequestError "
        "(bug), AuthenticationError (bad key), APIConnectionError (retry).",

        "ALWAYS store the Stripe customer ID (cus_*) in your user model and "
        "reuse it for all subsequent operations; NEVER create a new Customer "
        "object for each transaction.",

        "ALWAYS implement webhook event handlers for asynchronous events like "
        "payment_intent.succeeded, invoice.paid, and customer.subscription.deleted "
        "rather than relying solely on synchronous API responses.",

        "ALWAYS use test mode keys (sk_test_*, pk_test_*) in development and "
        "staging environments; NEVER use live keys outside production; store all "
        "keys in environment variables, never in source code.",

        "ALWAYS set expand parameters to fetch related objects in a single API "
        "call (e.g. expand=['latest_invoice.payment_intent']) instead of making "
        "multiple sequential requests.",

        "ALWAYS use Stripe's built-in metadata field to attach application-specific "
        "context (order_id, user_id, plan_name) to Stripe objects for "
        "reconciliation and debugging.",

        "ALWAYS implement proper refund handling with partial refund support; "
        "track refund status via webhooks (charge.refunded) rather than assuming "
        "immediate completion.",

        "ALWAYS use Stripe's API versioning by pinning the API version in your "
        "client initialization (stripe.api_version in Python, apiVersion in "
        "TypeScript) to prevent breaking changes from affecting production.",
    ),
    golden_examples={
        "checkout_session": (
            "import os\n"
            "import stripe\n"
            "\n"
            "stripe.api_key = os.environ[\"STRIPE_SECRET_KEY\"]\n"
            "stripe.api_version = \"2024-06-20\"\n"
            "\n"
            "\n"
            "def create_checkout_session(\n"
            "    customer_id: str,\n"
            "    price_id: str,\n"
            "    success_url: str,\n"
            "    cancel_url: str,\n"
            ") -> stripe.checkout.Session:\n"
            "    \"\"\"Create a Stripe Checkout Session for a subscription.\"\"\"\n"
            "    return stripe.checkout.Session.create(\n"
            "        customer=customer_id,\n"
            "        mode=\"subscription\",\n"
            "        line_items=[{\"price\": price_id, \"quantity\": 1}],\n"
            "        success_url=success_url + \"?session_id={CHECKOUT_SESSION_ID}\",\n"
            "        cancel_url=cancel_url,\n"
            "        metadata={\"customer_id\": customer_id},\n"
            "        subscription_data={\n"
            "            \"metadata\": {\"customer_id\": customer_id},\n"
            "        },\n"
            "    )"
        ),
        "webhook_handler": (
            "import os\n"
            "import stripe\n"
            "from fastapi import Request, HTTPException\n"
            "\n"
            "stripe.api_key = os.environ[\"STRIPE_SECRET_KEY\"]\n"
            "WEBHOOK_SECRET = os.environ[\"STRIPE_WEBHOOK_SECRET\"]\n"
            "\n"
            "\n"
            "async def handle_stripe_webhook(request: Request) -> dict:\n"
            "    \"\"\"Verify and process Stripe webhook events.\"\"\"\n"
            "    payload = await request.body()\n"
            "    sig_header = request.headers.get(\"stripe-signature\", \"\")\n"
            "\n"
            "    try:\n"
            "        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)\n"
            "    except stripe.error.SignatureVerificationError:\n"
            "        raise HTTPException(status_code=400, detail=\"Invalid signature\")\n"
            "\n"
            "    if event.type == \"payment_intent.succeeded\":\n"
            "        intent = event.data.object\n"
            "        await fulfill_order(intent.metadata.get(\"order_id\"))\n"
            "    elif event.type == \"customer.subscription.deleted\":\n"
            "        subscription = event.data.object\n"
            "        await cancel_user_subscription(subscription.customer)\n"
            "    elif event.type == \"invoice.payment_failed\":\n"
            "        invoice = event.data.object\n"
            "        await notify_payment_failure(invoice.customer)\n"
            "\n"
            "    return {\"status\": \"ok\"}"
        ),
        "client_integration": (
            "import Stripe from \"stripe\";\n"
            "\n"
            "const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {\n"
            "  apiVersion: \"2024-06-20\",\n"
            "  typescript: true,\n"
            "});\n"
            "\n"
            "export async function createPaymentIntent(\n"
            "  amount: number,\n"
            "  currency: string,\n"
            "  customerId: string,\n"
            "  orderId: string,\n"
            "): Promise<Stripe.PaymentIntent> {\n"
            "  return stripe.paymentIntents.create(\n"
            "    {\n"
            "      amount,\n"
            "      currency,\n"
            "      customer: customerId,\n"
            "      metadata: { order_id: orderId },\n"
            "      automatic_payment_methods: { enabled: true },\n"
            "    },\n"
            "    { idempotencyKey: `pi_${orderId}` },\n"
            "  );\n"
            "}"
        ),
    },
    setup_code={
        "python": (
            "import os\n"
            "import stripe\n"
            "\n"
            "stripe.api_key = os.environ[\"STRIPE_SECRET_KEY\"]\n"
            "stripe.api_version = \"2024-06-20\"\n"
            "stripe.max_network_retries = 2"
        ),
        "typescript": (
            "import Stripe from \"stripe\";\n"
            "\n"
            "export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {\n"
            "  apiVersion: \"2024-06-20\",\n"
            "  typescript: true,\n"
            "  maxNetworkRetries: 2,\n"
            "});"
        ),
    },
)

register_service(STRIPE)
