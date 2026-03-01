"""Firebase Firestore database configuration for schema generation."""

from app.agents.database_configs import DatabaseConfig, register_database

FIREBASE = DatabaseConfig(
    name="firebase",
    display_name="Firebase Firestore",
    category="document",
    ddl_dialect="none",
    default_port=443,
    supports_transactions=True,
    supports_migrations=False,
    type_mappings={
        "string": "string",
        "integer": "number",
        "text": "string",
        "boolean": "boolean",
        "float": "number",
        "decimal": "number",
        "datetime": "timestamp",
        "date": "timestamp",
        "json": "map",
        "uuid": "string",
        "binary": "bytes",
        "bigint": "number",
    },
    connection_templates={
        "typescript": (
            "// Firebase Admin SDK (server-side)\n"
            'import {{ initializeApp, cert }} from "firebase-admin/app";\n'
            'import {{ getFirestore }} from "firebase-admin/firestore";\n'
            "\n"
            "const app = initializeApp({{\n"
            "  credential: cert(serviceAccountKey),\n"
            "  projectId: '{project_id}',\n"
            "}});\n"
            "const db = getFirestore(app);\n"
            "\n"
            "// Client SDK (browser / mobile)\n"
            'import {{ initializeApp }} from "firebase/app";\n'
            'import {{ getFirestore }} from "firebase/firestore";\n'
            "\n"
            "const firebaseConfig = {{\n"
            "  apiKey: '{api_key}',\n"
            "  authDomain: '{project_id}.firebaseapp.com',\n"
            "  projectId: '{project_id}',\n"
            "}};\n"
            "const app = initializeApp(firebaseConfig);\n"
            "const db = getFirestore(app);"
        ),
        "python": (
            "import firebase_admin\n"
            "from firebase_admin import credentials, firestore\n"
            "\n"
            "cred = credentials.Certificate('serviceAccountKey.json')\n"
            "firebase_admin.initialize_app(cred, {{\n"
            "    'projectId': '{project_id}',\n"
            "}})\n"
            "db = firestore.client()"
        ),
        "java": (
            "import com.google.cloud.firestore.Firestore;\n"
            "import com.google.firebase.FirebaseApp;\n"
            "import com.google.firebase.FirebaseOptions;\n"
            "import com.google.firebase.cloud.FirestoreClient;\n"
            "\n"
            "FirebaseOptions options = FirebaseOptions.builder()\n"
            '    .setCredentials(GoogleCredentials.getApplicationDefault())\n'
            '    .setProjectId("{project_id}")\n'
            "    .build();\n"
            "FirebaseApp.initializeApp(options);\n"
            "Firestore db = FirestoreClient.getFirestore();"
        ),
    },
    index_types=(
        "single_field",
        "composite",
        "collection_group",
    ),
    rules=(
        "Structure data as collections of documents. Each document has a "
        "maximum size of 1 MiB -- keep documents small and focused.",
        "Use subcollections for hierarchical data that can grow without "
        "limit (e.g. /users/{uid}/orders) instead of nested arrays.",
        "Denormalize data aggressively: duplicate fields across documents "
        "to avoid multi-document reads, since Firestore charges per read.",
        "Design your data model around your queries first -- Firestore "
        "does not support arbitrary joins or server-side aggregation.",
        "Write Firestore Security Rules to enforce authentication and "
        "authorization at the document and field level.",
        "Use composite indexes for queries that filter or order on multiple "
        "fields; Firestore requires explicit indexes for these.",
        "Avoid document IDs that increase monotonically (e.g. timestamps) "
        "to prevent write hotspots on a single storage shard.",
        "Use batched writes or transactions for atomic multi-document "
        "operations; a batch can contain up to 500 operations.",
        "Store timestamps as Firestore Timestamp objects (not epoch "
        "numbers or ISO strings) for proper ordering and querying.",
        "Use collection group queries to search across all subcollections "
        "with the same name; create a collection-group index first.",
        "Keep document field count reasonable (under ~200 fields) and "
        "avoid deeply nested maps beyond 20 levels.",
        "Use Firestore offline persistence for mobile and web apps so "
        "reads and writes work seamlessly while offline.",
        "Paginate large result sets with limit() and startAfter() using "
        "document snapshots or field values as cursors.",
        "Use Cloud Functions triggers (onCreate, onUpdate, onDelete) for "
        "server-side logic instead of complex client-side workflows.",
    ),
    ddl_examples={
        "security_rules": (
            "rules_version = '2';\n"
            "service cloud.firestore {\n"
            "  match /databases/{database}/documents {\n"
            "\n"
            "    match /users/{userId} {\n"
            "      allow read: if request.auth != null;\n"
            "      allow write: if request.auth.uid == userId;\n"
            "\n"
            "      match /orders/{orderId} {\n"
            "        allow read: if request.auth.uid == userId;\n"
            "        allow create: if request.auth.uid == userId\n"
            "          && request.resource.data.keys().hasAll(['total', 'items', 'created_at'])\n"
            "          && request.resource.data.total is number;\n"
            "      }\n"
            "    }\n"
            "\n"
            "    match /products/{productId} {\n"
            "      allow read: if true;\n"
            "      allow write: if request.auth.token.admin == true;\n"
            "    }\n"
            "  }\n"
            "}"
        ),
        "composite_index": (
            "// firestore.indexes.json\n"
            "{\n"
            '  "indexes": [\n'
            "    {\n"
            '      "collectionGroup": "orders",\n'
            '      "queryScope": "COLLECTION",\n'
            '      "fields": [\n'
            '        { "fieldPath": "status", "order": "ASCENDING" },\n'
            '        { "fieldPath": "created_at", "order": "DESCENDING" }\n'
            "      ]\n"
            "    },\n"
            "    {\n"
            '      "collectionGroup": "orders",\n'
            '      "queryScope": "COLLECTION_GROUP",\n'
            '      "fields": [\n'
            '        { "fieldPath": "customer_id", "order": "ASCENDING" },\n'
            '        { "fieldPath": "total", "order": "DESCENDING" }\n'
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}"
        ),
        "document_schema": (
            "// TypeScript interface describing the Firestore document schema\n"
            "interface User {\n"
            "  uid: string;                     // matches Auth UID\n"
            "  email: string;\n"
            "  displayName: string;\n"
            "  photoURL?: string;\n"
            "  roles: string[];                 // e.g. ['admin', 'editor']\n"
            "  preferences: {\n"
            "    theme: 'light' | 'dark';\n"
            "    notifications: boolean;\n"
            "  };\n"
            "  createdAt: Timestamp;\n"
            "  updatedAt: Timestamp;\n"
            "}\n"
            "\n"
            "interface Order {\n"
            "  orderId: string;\n"
            "  customerId: string;              // reference to users/{uid}\n"
            "  items: Array<{\n"
            "    productId: string;\n"
            "    quantity: number;\n"
            "    price: number;\n"
            "  }>;\n"
            "  total: number;\n"
            "  status: 'pending' | 'processing' | 'shipped' | 'delivered';\n"
            "  createdAt: Timestamp;\n"
            "}"
        ),
    },
    orm_patterns={
        "nextjs": "firebase-admin",
        "express": "firebase-admin",
    },
)

register_database(FIREBASE)
