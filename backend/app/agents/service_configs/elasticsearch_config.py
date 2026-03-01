"""Elasticsearch / OpenSearch configuration for service integration."""

from app.agents.service_configs import ServiceConfig, register_service

ELASTICSEARCH = ServiceConfig(
    name="elasticsearch",
    display_name="Elasticsearch",
    category="search",
    supported_languages=("python", "typescript"),
    package_dependencies={
        "python": ("elasticsearch",),
        "typescript": ("@elastic/elasticsearch",),
    },
    environment_variables=(
        "ELASTICSEARCH_URL",
        "ELASTICSEARCH_API_KEY",
    ),
    rules=(
        "ALWAYS define explicit index mappings with field types before indexing "
        "documents; NEVER rely on dynamic mapping in production as it creates "
        "unpredictable field types and mapping explosions from nested objects.",

        "ALWAYS use the Bulk API (/_bulk) for indexing more than a single "
        "document; individual index requests create excessive HTTP overhead "
        "and are orders of magnitude slower for batch operations.",

        "ALWAYS set a refresh_interval appropriate to the use case: 1s for "
        "near-real-time search, 30s+ for heavy write workloads, and -1 during "
        "bulk reindex operations to maximise throughput.",

        "ALWAYS use keyword type for fields that need exact-match filtering, "
        "sorting, or aggregation (e.g. status, category, user_id); use text "
        "type only for fields that require full-text search analysis.",

        "ALWAYS implement index aliases (e.g. 'products' pointing to "
        "'products_v2') to enable zero-downtime reindexing; NEVER point "
        "application code at physical index names directly.",

        "ALWAYS paginate large result sets using search_after with a sort tiebreaker "
        "field (_id or a unique field); NEVER use from/size pagination beyond "
        "10,000 results as it degrades performance and hits the index.max_result_window.",

        "ALWAYS use the multi_match query with 'best_fields' or 'cross_fields' "
        "type for user-facing search inputs instead of building complex bool "
        "queries manually; boost relevant fields with the ^N syntax.",

        "ALWAYS configure Index Lifecycle Management (ILM) policies for "
        "time-series and log indices to automatically roll over, shrink, and "
        "delete old indices based on age or size thresholds.",

        "ALWAYS use source filtering (_source: ['field1', 'field2']) to return "
        "only the fields needed by the client; fetching the full _source wastes "
        "network bandwidth and serialization time on large documents.",

        "ALWAYS handle Elasticsearch connection errors and timeouts with retry "
        "logic and circuit breakers; configure request_timeout, max_retries, "
        "and sniff_on_connection_fail for production resilience.",
    ),
    golden_examples={
        "index_document": (
            "import os\n"
            "from elasticsearch import Elasticsearch\n"
            "\n"
            "es = Elasticsearch(\n"
            "    os.environ[\"ELASTICSEARCH_URL\"],\n"
            "    api_key=os.environ[\"ELASTICSEARCH_API_KEY\"],\n"
            "    request_timeout=30,\n"
            "    max_retries=3,\n"
            "    retry_on_timeout=True,\n"
            ")\n"
            "\n"
            "INDEX_ALIAS = \"products\"\n"
            "\n"
            "\n"
            "def index_product(product_id: str, data: dict) -> dict:\n"
            "    \"\"\"Index a single product document.\"\"\"\n"
            "    return es.index(\n"
            "        index=INDEX_ALIAS,\n"
            "        id=product_id,\n"
            "        document={\n"
            "            \"name\": data[\"name\"],\n"
            "            \"description\": data[\"description\"],\n"
            "            \"category\": data[\"category\"],\n"
            "            \"price\": data[\"price\"],\n"
            "            \"in_stock\": data[\"in_stock\"],\n"
            "            \"updated_at\": data[\"updated_at\"],\n"
            "        },\n"
            "    )"
        ),
        "search_query": (
            "import os\n"
            "from elasticsearch import Elasticsearch\n"
            "\n"
            "es = Elasticsearch(\n"
            "    os.environ[\"ELASTICSEARCH_URL\"],\n"
            "    api_key=os.environ[\"ELASTICSEARCH_API_KEY\"],\n"
            ")\n"
            "\n"
            "\n"
            "def search_products(\n"
            "    query: str,\n"
            "    category: str | None = None,\n"
            "    min_price: float | None = None,\n"
            "    max_price: float | None = None,\n"
            "    page_size: int = 20,\n"
            "    search_after: list | None = None,\n"
            ") -> dict:\n"
            "    \"\"\"Full-text search with filters and cursor pagination.\"\"\"\n"
            "    must = [\n"
            "        {\"multi_match\": {\n"
            "            \"query\": query,\n"
            "            \"fields\": [\"name^3\", \"description\"],\n"
            "            \"type\": \"best_fields\",\n"
            "            \"fuzziness\": \"AUTO\",\n"
            "        }},\n"
            "    ]\n"
            "    filters = [{\"term\": {\"in_stock\": True}}]\n"
            "    if category:\n"
            "        filters.append({\"term\": {\"category\": category}})\n"
            "    if min_price is not None or max_price is not None:\n"
            "        price_range = {}\n"
            "        if min_price is not None:\n"
            "            price_range[\"gte\"] = min_price\n"
            "        if max_price is not None:\n"
            "            price_range[\"lte\"] = max_price\n"
            "        filters.append({\"range\": {\"price\": price_range}})\n"
            "\n"
            "    body = {\n"
            "        \"query\": {\"bool\": {\"must\": must, \"filter\": filters}},\n"
            "        \"sort\": [{\"_score\": \"desc\"}, {\"_id\": \"asc\"}],\n"
            "        \"size\": page_size,\n"
            "        \"_source\": [\"name\", \"category\", \"price\", \"in_stock\"],\n"
            "    }\n"
            "    if search_after:\n"
            "        body[\"search_after\"] = search_after\n"
            "\n"
            "    return es.search(index=\"products\", body=body)"
        ),
        "bulk_index": (
            "import { Client } from \"@elastic/elasticsearch\";\n"
            "\n"
            "const client = new Client({\n"
            "  node: process.env.ELASTICSEARCH_URL!,\n"
            "  auth: { apiKey: process.env.ELASTICSEARCH_API_KEY! },\n"
            "  maxRetries: 3,\n"
            "  requestTimeout: 30_000,\n"
            "});\n"
            "\n"
            "interface Product {\n"
            "  id: string;\n"
            "  name: string;\n"
            "  description: string;\n"
            "  category: string;\n"
            "  price: number;\n"
            "}\n"
            "\n"
            "export async function bulkIndexProducts(\n"
            "  products: Product[],\n"
            "): Promise<{ indexed: number; errors: number }> {\n"
            "  const operations = products.flatMap((p) => [\n"
            "    { index: { _index: \"products\", _id: p.id } },\n"
            "    { name: p.name, description: p.description,\n"
            "      category: p.category, price: p.price },\n"
            "  ]);\n"
            "\n"
            "  const { errors, items } = await client.bulk({\n"
            "    refresh: false,\n"
            "    operations,\n"
            "  });\n"
            "\n"
            "  const errorCount = items.filter(\n"
            "    (i) => i.index?.error,\n"
            "  ).length;\n"
            "\n"
            "  return { indexed: products.length - errorCount, errors: errorCount };\n"
            "}"
        ),
    },
    setup_code={
        "python": (
            "import os\n"
            "from elasticsearch import Elasticsearch\n"
            "\n"
            "es_client = Elasticsearch(\n"
            "    os.environ[\"ELASTICSEARCH_URL\"],\n"
            "    api_key=os.environ[\"ELASTICSEARCH_API_KEY\"],\n"
            "    request_timeout=30,\n"
            "    max_retries=3,\n"
            "    retry_on_timeout=True,\n"
            "    sniff_on_connection_fail=True,\n"
            ")"
        ),
        "typescript": (
            "import { Client } from \"@elastic/elasticsearch\";\n"
            "\n"
            "export const esClient = new Client({\n"
            "  node: process.env.ELASTICSEARCH_URL!,\n"
            "  auth: { apiKey: process.env.ELASTICSEARCH_API_KEY! },\n"
            "  maxRetries: 3,\n"
            "  requestTimeout: 30_000,\n"
            "  sniffOnConnectionFault: true,\n"
            "});"
        ),
    },
)

register_service(ELASTICSEARCH)
