"""AWS S3 / compatible object storage configuration for service integration."""

from app.agents.service_configs import ServiceConfig, register_service

S3 = ServiceConfig(
    name="s3",
    display_name="Amazon S3",
    category="storage",
    supported_languages=("python", "typescript"),
    package_dependencies={
        "python": ("boto3", "botocore"),
        "typescript": ("@aws-sdk/client-s3", "@aws-sdk/s3-request-presigner"),
    },
    environment_variables=(
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "S3_BUCKET_NAME",
        "AWS_REGION",
    ),
    rules=(
        "ALWAYS use presigned URLs for client-side uploads and downloads instead "
        "of proxying file bytes through your application server; presigned URLs "
        "offload bandwidth to S3 and expire after a configurable TTL.",

        "ALWAYS validate Content-Type and file extension on the server before "
        "generating a presigned upload URL; NEVER trust the client-reported "
        "Content-Type alone -- verify it against an allowlist of MIME types.",

        "ALWAYS enforce a maximum file size using Content-Length-Range conditions "
        "in presigned POST policies or by setting upload size limits in your API "
        "to prevent abuse and unexpected storage costs.",

        "NEVER construct S3 keys from user input without sanitisation; strip "
        "path traversal sequences (../) and special characters, and prefix "
        "keys with a tenant/user namespace (e.g. uploads/{tenant_id}/{uuid}).",

        "ALWAYS set lifecycle rules on the bucket to transition infrequently "
        "accessed objects to S3 Intelligent-Tiering or Glacier, and to expire "
        "temporary upload prefixes after a defined retention period.",

        "ALWAYS enable server-side encryption (SSE-S3 or SSE-KMS) on the bucket "
        "by default; set the bucket policy to deny PutObject requests that lack "
        "the x-amz-server-side-encryption header.",

        "ALWAYS use the S3 Transfer Acceleration endpoint for large file uploads "
        "from geographically distributed clients; enable it on the bucket and "
        "use the accelerated endpoint URL in presigned URLs.",

        "ALWAYS set appropriate Cache-Control and Content-Disposition headers "
        "when uploading objects that will be served to browsers; use 'inline' "
        "for images and 'attachment' for downloadable files.",

        "ALWAYS use multipart upload (via S3 managed upload or Transfer Manager) "
        "for files larger than 100 MB to enable parallel part uploads, automatic "
        "retries per part, and resumable uploads.",

        "ALWAYS configure CORS on the bucket to allow only your application's "
        "origin(s) for PUT and POST methods; a wildcard CORS policy exposes the "
        "bucket to cross-site upload abuse.",
    ),
    golden_examples={
        "upload_file": (
            "import os\n"
            "import uuid\n"
            "import boto3\n"
            "from botocore.config import Config\n"
            "\n"
            "s3 = boto3.client(\n"
            "    \"s3\",\n"
            "    region_name=os.environ[\"AWS_REGION\"],\n"
            "    config=Config(signature_version=\"s3v4\"),\n"
            ")\n"
            "BUCKET = os.environ[\"S3_BUCKET_NAME\"]\n"
            "\n"
            "\n"
            "def upload_file(\n"
            "    tenant_id: str,\n"
            "    file_bytes: bytes,\n"
            "    content_type: str,\n"
            "    original_filename: str,\n"
            ") -> str:\n"
            "    \"\"\"Upload a file to S3 and return the object key.\"\"\"\n"
            "    ext = original_filename.rsplit(\".\", 1)[-1].lower()\n"
            "    key = f\"uploads/{tenant_id}/{uuid.uuid4()}.{ext}\"\n"
            "    s3.put_object(\n"
            "        Bucket=BUCKET,\n"
            "        Key=key,\n"
            "        Body=file_bytes,\n"
            "        ContentType=content_type,\n"
            "        ServerSideEncryption=\"AES256\",\n"
            "        Metadata={\"original_name\": original_filename},\n"
            "    )\n"
            "    return key"
        ),
        "presigned_url": (
            "import os\n"
            "import boto3\n"
            "from botocore.config import Config\n"
            "\n"
            "s3 = boto3.client(\n"
            "    \"s3\",\n"
            "    region_name=os.environ[\"AWS_REGION\"],\n"
            "    config=Config(signature_version=\"s3v4\"),\n"
            ")\n"
            "BUCKET = os.environ[\"S3_BUCKET_NAME\"]\n"
            "\n"
            "\n"
            "def generate_presigned_upload_url(\n"
            "    key: str,\n"
            "    content_type: str,\n"
            "    expires_in: int = 3600,\n"
            ") -> str:\n"
            "    \"\"\"Generate a presigned PUT URL for client-side upload.\"\"\"\n"
            "    return s3.generate_presigned_url(\n"
            "        ClientMethod=\"put_object\",\n"
            "        Params={\n"
            "            \"Bucket\": BUCKET,\n"
            "            \"Key\": key,\n"
            "            \"ContentType\": content_type,\n"
            "            \"ServerSideEncryption\": \"AES256\",\n"
            "        },\n"
            "        ExpiresIn=expires_in,\n"
            "    )\n"
            "\n"
            "\n"
            "def generate_presigned_download_url(\n"
            "    key: str,\n"
            "    expires_in: int = 3600,\n"
            ") -> str:\n"
            "    \"\"\"Generate a presigned GET URL for client-side download.\"\"\"\n"
            "    return s3.generate_presigned_url(\n"
            "        ClientMethod=\"get_object\",\n"
            "        Params={\"Bucket\": BUCKET, \"Key\": key},\n"
            "        ExpiresIn=expires_in,\n"
            "    )"
        ),
        "delete_file": (
            "import { S3Client, DeleteObjectCommand } from \"@aws-sdk/client-s3\";\n"
            "\n"
            "const s3 = new S3Client({ region: process.env.AWS_REGION! });\n"
            "\n"
            "export async function deleteFile(\n"
            "  key: string,\n"
            "): Promise<void> {\n"
            "  await s3.send(\n"
            "    new DeleteObjectCommand({\n"
            "      Bucket: process.env.S3_BUCKET_NAME!,\n"
            "      Key: key,\n"
            "    }),\n"
            "  );\n"
            "}\n"
            "\n"
            "export async function deleteFiles(\n"
            "  keys: string[],\n"
            "): Promise<void> {\n"
            "  // S3 DeleteObjects supports up to 1000 keys per request\n"
            "  const { DeleteObjectsCommand } = await import(\"@aws-sdk/client-s3\");\n"
            "  const batchSize = 1000;\n"
            "  for (let i = 0; i < keys.length; i += batchSize) {\n"
            "    await s3.send(\n"
            "      new DeleteObjectsCommand({\n"
            "        Bucket: process.env.S3_BUCKET_NAME!,\n"
            "        Delete: {\n"
            "          Objects: keys.slice(i, i + batchSize).map((k) => ({ Key: k })),\n"
            "        },\n"
            "      }),\n"
            "    );\n"
            "  }\n"
            "}"
        ),
    },
    setup_code={
        "python": (
            "import os\n"
            "import boto3\n"
            "from botocore.config import Config\n"
            "\n"
            "s3_client = boto3.client(\n"
            "    \"s3\",\n"
            "    region_name=os.environ[\"AWS_REGION\"],\n"
            "    config=Config(\n"
            "        signature_version=\"s3v4\",\n"
            "        retries={\"max_attempts\": 3, \"mode\": \"adaptive\"},\n"
            "    ),\n"
            ")\n"
            "S3_BUCKET = os.environ[\"S3_BUCKET_NAME\"]"
        ),
        "typescript": (
            "import { S3Client } from \"@aws-sdk/client-s3\";\n"
            "\n"
            "export const s3 = new S3Client({\n"
            "  region: process.env.AWS_REGION!,\n"
            "  maxAttempts: 3,\n"
            "});\n"
            "\n"
            "export const S3_BUCKET = process.env.S3_BUCKET_NAME!;"
        ),
    },
)

register_service(S3)
