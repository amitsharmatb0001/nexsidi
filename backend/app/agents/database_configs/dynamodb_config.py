"""Amazon DynamoDB database configuration for schema generation."""

from app.agents.database_configs import DatabaseConfig, register_database

DYNAMODB = DatabaseConfig(
    name="dynamodb",
    display_name="Amazon DynamoDB",
    category="key_value",
    ddl_dialect="none",
    default_port=443,
    supports_transactions=True,
    supports_migrations=False,
    type_mappings={
        "string": "S",
        "integer": "N",
        "text": "S",
        "boolean": "BOOL",
        "float": "N",
        "decimal": "N",
        "datetime": "S",
        "date": "S",
        "json": "M",
        "uuid": "S",
        "binary": "B",
        "bigint": "N",
    },
    connection_templates={
        "python": (
            "import boto3\n"
            "\n"
            "# Using default credential chain (env vars, ~/.aws, IAM role)\n"
            "dynamodb = boto3.resource(\n"
            "    'dynamodb',\n"
            "    region_name='{region}',\n"
            "    endpoint_url='{endpoint}',  # omit for production AWS\n"
            ")\n"
            "table = dynamodb.Table('{table_name}')\n"
            "\n"
            "# Low-level client (for advanced operations)\n"
            "client = boto3.client('dynamodb', region_name='{region}')"
        ),
        "typescript": (
            'import {{ DynamoDBClient }} from "@aws-sdk/client-dynamodb";\n'
            'import {{ DynamoDBDocumentClient, GetCommand, PutCommand }} '
            'from "@aws-sdk/lib-dynamodb";\n'
            "\n"
            "const client = new DynamoDBClient({{ region: '{region}' }});\n"
            "const docClient = DynamoDBDocumentClient.from(client);"
        ),
        "java": (
            "import software.amazon.awssdk.regions.Region;\n"
            "import software.amazon.awssdk.services.dynamodb.DynamoDbClient;\n"
            "import software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient;\n"
            "\n"
            "DynamoDbClient client = DynamoDbClient.builder()\n"
            "    .region(Region.of(\"{region}\"))\n"
            "    .build();\n"
            "DynamoDbEnhancedClient enhancedClient = DynamoDbEnhancedClient.builder()\n"
            "    .dynamoDbClient(client)\n"
            "    .build();"
        ),
        "go": (
            'import (\n'
            '    "github.com/aws/aws-sdk-go-v2/config"\n'
            '    "github.com/aws/aws-sdk-go-v2/service/dynamodb"\n'
            ")\n"
            "\n"
            "cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion(\"{region}\"))\n"
            "client := dynamodb.NewFromConfig(cfg)"
        ),
    },
    index_types=(
        "partition_key",
        "sort_key",
        "gsi",
        "lsi",
    ),
    rules=(
        "Design tables around access patterns first. Identify every read "
        "and write pattern before choosing partition and sort keys.",
        "Choose a partition key with high cardinality and even distribution "
        "to avoid hot partitions (e.g. user_id, tenant_id, not status).",
        "Use composite sort keys (e.g. 'ORDER#2024-01-15#abc123') to "
        "enable flexible range queries within a single partition.",
        "Prefer single-table design: store multiple entity types in one "
        "table using prefixed keys (PK='USER#123', SK='PROFILE') to "
        "minimize the number of requests.",
        "Create Global Secondary Indexes (GSI) for access patterns that "
        "cannot be served by the base table keys; each GSI is a full "
        "copy of projected attributes.",
        "Use Local Secondary Indexes (LSI) only when you need an "
        "alternative sort key within the same partition; LSIs must be "
        "defined at table creation time.",
        "Keep items under 400 KB. For large objects, store a reference "
        "to S3 in the DynamoDB item instead of the blob itself.",
        "Use conditional writes (ConditionExpression) for optimistic "
        "locking and to prevent overwriting stale data.",
        "Enable TTL on a numeric epoch attribute to automatically expire "
        "and delete old items without consuming write capacity.",
        "Use DynamoDB Streams for event-driven architectures and "
        "cross-region replication via Global Tables.",
        "Avoid scan operations in production -- always query by partition "
        "key. If a scan is unavoidable, use parallel scan segments.",
        "Use batch operations (BatchGetItem, BatchWriteItem) for bulk "
        "reads and writes; each batch supports up to 25 items.",
        "Use TransactWriteItems for ACID transactions across up to 100 "
        "items; prefer single-item conditional writes when possible.",
    ),
    ddl_examples={
        "create_table": (
            "// AWS CLI / CloudFormation-style table definition\n"
            "{\n"
            '  "TableName": "app_entities",\n'
            '  "KeySchema": [\n'
            '    { "AttributeName": "PK", "KeyType": "HASH" },\n'
            '    { "AttributeName": "SK", "KeyType": "RANGE" }\n'
            "  ],\n"
            '  "AttributeDefinitions": [\n'
            '    { "AttributeName": "PK", "AttributeType": "S" },\n'
            '    { "AttributeName": "SK", "AttributeType": "S" },\n'
            '    { "AttributeName": "GSI1PK", "AttributeType": "S" },\n'
            '    { "AttributeName": "GSI1SK", "AttributeType": "S" }\n'
            "  ],\n"
            '  "BillingMode": "PAY_PER_REQUEST"\n'
            "}"
        ),
        "global_secondary_index": (
            "// GSI for querying orders by status and date\n"
            "{\n"
            '  "IndexName": "GSI1",\n'
            '  "KeySchema": [\n'
            '    { "AttributeName": "GSI1PK", "KeyType": "HASH" },\n'
            '    { "AttributeName": "GSI1SK", "KeyType": "RANGE" }\n'
            "  ],\n"
            '  "Projection": {\n'
            '    "ProjectionType": "ALL"\n'
            "  }\n"
            "}\n"
            "\n"
            "// Example key overloading:\n"
            "// GSI1PK = 'STATUS#shipped'   GSI1SK = '2024-01-15T10:30:00Z'\n"
            "// GSI1PK = 'CUSTOMER#abc123'  GSI1SK = 'ORDER#2024-01-15'"
        ),
        "ttl_configuration": (
            "// Enable TTL on the 'expires_at' attribute\n"
            "// Items are automatically deleted after the epoch timestamp\n"
            "{\n"
            '  "TableName": "sessions",\n'
            '  "TimeToLiveSpecification": {\n'
            '    "Enabled": true,\n'
            '    "AttributeName": "expires_at"\n'
            "  }\n"
            "}\n"
            "\n"
            "// Example item with TTL:\n"
            "// { PK: 'SESSION#xyz', SK: 'META', expires_at: 1706000000 }"
        ),
    },
    orm_patterns={
        "fastapi": "boto3",
        "express": "aws-sdk-v3",
    },
)

register_database(DYNAMODB)
