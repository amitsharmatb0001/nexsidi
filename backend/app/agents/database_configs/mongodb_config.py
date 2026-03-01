"""MongoDB database configuration for schema generation."""

from app.agents.database_configs import DatabaseConfig, register_database

MONGODB = DatabaseConfig(
    name="mongodb",
    display_name="MongoDB",
    category="document",
    ddl_dialect="none",
    default_port=27017,
    supports_transactions=True,
    supports_migrations=False,
    type_mappings={
        "string": "String",
        "integer": "Int32",
        "text": "String",
        "boolean": "Boolean",
        "float": "Double",
        "decimal": "Decimal128",
        "datetime": "Date",
        "date": "Date",
        "json": "Object",
        "uuid": "UUID",
        "binary": "BinData",
        "bigint": "Long",
    },
    connection_templates={
        "python": (
            "# pymongo (sync)\n"
            "from pymongo import MongoClient\n"
            'client = MongoClient("mongodb://{user}:{password}@{host}:{port}/{database}'
            '?authSource=admin&retryWrites=true&w=majority")\n'
            "db = client['{database}']\n"
            "\n"
            "# motor (async)\n"
            "from motor.motor_asyncio import AsyncIOMotorClient\n"
            'client = AsyncIOMotorClient("mongodb://{user}:{password}@{host}:{port}/{database}'
            '?authSource=admin&retryWrites=true&w=majority")\n'
            "db = client['{database}']"
        ),
        "typescript": (
            'import mongoose from "mongoose";\n'
            "\n"
            "await mongoose.connect(\n"
            '  "mongodb://{user}:{password}@{host}:{port}/{database}'
            '?authSource=admin&retryWrites=true&w=majority"\n'
            ");"
        ),
        "java": (
            "import com.mongodb.client.MongoClients;\n"
            "import com.mongodb.client.MongoClient;\n"
            "import com.mongodb.client.MongoDatabase;\n"
            "\n"
            'MongoClient client = MongoClients.create(\n'
            '    "mongodb://{user}:{password}@{host}:{port}/{database}'
            '?authSource=admin&retryWrites=true&w=majority"\n'
            ");\n"
            'MongoDatabase db = client.getDatabase("{database}");'
        ),
        "go": (
            'import "go.mongodb.org/mongo-driver/mongo"\n'
            'import "go.mongodb.org/mongo-driver/mongo/options"\n'
            "\n"
            'uri := "mongodb://{user}:{password}@{host}:{port}/{database}'
            '?authSource=admin&retryWrites=true&w=majority"\n'
            "client, err := mongo.Connect(ctx, options.Client().ApplyURI(uri))\n"
            'db := client.Database("{database}")'
        ),
    },
    index_types=(
        "single_field",
        "compound",
        "text",
        "geospatial",
        "hashed",
        "wildcard",
    ),
    rules=(
        "Design documents around query access patterns, not entity "
        "relationships -- embed data that is read together.",
        "Prefer embedding for one-to-few relationships and data that does "
        "not change independently; use references ($ref or manual ObjectId) "
        "for one-to-many / many-to-many or highly volatile subdocuments.",
        "Every document gets an _id field automatically (ObjectId by "
        "default). Never reuse or recycle _id values.",
        "Use the aggregation pipeline ($match, $group, $project, $lookup) "
        "for complex queries instead of pulling data to the application.",
        "Create indexes that match your most frequent query filters and "
        "sort orders; compound indexes follow the ESR (Equality, Sort, "
        "Range) rule.",
        "Add schema validation (JSON Schema validator on the collection) to "
        "enforce required fields and types at the database level.",
        "Keep individual documents under 16 MB. If a document could grow "
        "unboundedly (e.g. comments), use the bucket or outlier pattern.",
        "Use $lookup sparingly -- it is essentially a left outer join and "
        "can be expensive; denormalize where possible.",
        "Store dates as native Date (ISODate) objects, not strings, so "
        "range queries and aggregation date operators work correctly.",
        "Use the change-stream API (watch()) for real-time event-driven "
        "architectures instead of polling.",
        "For time-series workloads, use MongoDB time-series collections "
        "with the metaField and timeField options.",
        "Always specify write concern 'majority' and read concern "
        "'majority' in production for strong consistency.",
        "Use bulk operations (insertMany, bulkWrite) for batch writes to "
        "minimize round trips and improve throughput.",
    ),
    ddl_examples={
        "create_collection_with_validator": (
            "db.createCollection('users', {\n"
            "  validator: {\n"
            "    $jsonSchema: {\n"
            "      bsonType: 'object',\n"
            "      required: ['email', 'name', 'created_at'],\n"
            "      properties: {\n"
            "        email:      { bsonType: 'string', description: 'must be a string' },\n"
            "        name:       { bsonType: 'string' },\n"
            "        age:        { bsonType: 'int', minimum: 0 },\n"
            "        created_at: { bsonType: 'date' },\n"
            "        roles:      { bsonType: 'array', items: { bsonType: 'string' } }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "});"
        ),
        "create_indexes": (
            "// Unique index on email\n"
            "db.users.createIndex({ email: 1 }, { unique: true });\n"
            "\n"
            "// Compound index following ESR rule\n"
            "db.orders.createIndex({ status: 1, created_at: -1, total: 1 });\n"
            "\n"
            "// Text index for full-text search\n"
            "db.articles.createIndex({ title: 'text', body: 'text' });"
        ),
        "aggregation_pipeline": (
            "db.orders.aggregate([\n"
            "  { $match: { status: 'completed' } },\n"
            "  { $group: {\n"
            "      _id: '$customer_id',\n"
            "      total_spent: { $sum: '$amount' },\n"
            "      order_count: { $sum: 1 }\n"
            "  }},\n"
            "  { $sort: { total_spent: -1 } },\n"
            "  { $limit: 10 }\n"
            "]);"
        ),
    },
    orm_patterns={
        "fastapi": "Motor+Beanie",
        "express": "Mongoose",
        "django": "Djongo",
    },
)

register_database(MONGODB)
