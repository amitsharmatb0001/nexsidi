"""BullMQ distributed task queue configuration for service integration."""

from app.agents.service_configs import ServiceConfig, register_service

BULLMQ = ServiceConfig(
    name="bullmq",
    display_name="BullMQ",
    category="task_queue",
    supported_languages=("typescript",),
    package_dependencies={
        "typescript": ("bullmq", "ioredis"),
    },
    environment_variables=(
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
    ),
    rules=(
        "ALWAYS create a shared IORedis connection instance and pass it to both "
        "Queue and Worker constructors; NEVER let BullMQ create implicit "
        "connections, as this leads to connection leaks under load.",

        "ALWAYS set removeOnComplete and removeOnFail options with a count or "
        "age limit (e.g. { count: 1000 }) on every queue to prevent Redis "
        "memory from growing unbounded with completed job metadata.",

        "ALWAYS define a concurrency limit on the Worker constructor to control "
        "how many jobs run in parallel per worker process; default to 5 and "
        "tune based on whether jobs are CPU-bound or I/O-bound.",

        "ALWAYS implement job retry with exponential backoff using the "
        "attempts and backoff options ({ type: 'exponential', delay: 1000 }) "
        "on every job added to the queue.",

        "NEVER pass large payloads (files, buffers, full documents) as job "
        "data; pass only IDs or S3 keys and fetch the data inside the worker "
        "processor to keep Redis memory usage low.",

        "ALWAYS use named processors or separate queues for different job types "
        "rather than a single catch-all queue with if/else branching inside "
        "the processor function.",

        "ALWAYS use QueueScheduler (or the built-in repeat option) for "
        "recurring jobs instead of external cron; define repeat patterns with "
        "{ every: ms } or { cron: '...' } so schedules are code-managed.",

        "ALWAYS handle the Worker 'failed' and 'error' events to log failures "
        "and trigger alerts; unhandled worker errors cause silent job loss.",

        "ALWAYS use job priorities (priority option, lower number = higher "
        "priority) when mixing urgent and background work in the same queue "
        "to ensure critical jobs are processed first.",

        "ALWAYS call queue.close() and worker.close() in the process shutdown "
        "handler (SIGTERM/SIGINT) to allow in-flight jobs to complete "
        "gracefully before the process exits.",
    ),
    golden_examples={
        "queue": (
            "import { Queue } from \"bullmq\";\n"
            "import IORedis from \"ioredis\";\n"
            "\n"
            "const connection = new IORedis(process.env.REDIS_URL!, {\n"
            "  maxRetriesPerRequest: null,\n"
            "});\n"
            "\n"
            "export const emailQueue = new Queue(\"email\", {\n"
            "  connection,\n"
            "  defaultJobOptions: {\n"
            "    attempts: 5,\n"
            "    backoff: { type: \"exponential\", delay: 1000 },\n"
            "    removeOnComplete: { count: 1000 },\n"
            "    removeOnFail: { count: 5000 },\n"
            "  },\n"
            "});\n"
            "\n"
            "export async function enqueueEmail(\n"
            "  to: string,\n"
            "  templateId: string,\n"
            "  data: Record<string, unknown>,\n"
            "): Promise<string> {\n"
            "  const job = await emailQueue.add(\"send\", { to, templateId, data }, {\n"
            "    priority: 1,\n"
            "  });\n"
            "  return job.id!;\n"
            "}"
        ),
        "worker": (
            "import { Worker, Job } from \"bullmq\";\n"
            "import IORedis from \"ioredis\";\n"
            "import { logger } from \"./logger\";\n"
            "\n"
            "const connection = new IORedis(process.env.REDIS_URL!, {\n"
            "  maxRetriesPerRequest: null,\n"
            "});\n"
            "\n"
            "const emailWorker = new Worker(\n"
            "  \"email\",\n"
            "  async (job: Job) => {\n"
            "    logger.info({ jobId: job.id, to: job.data.to }, \"Processing email\");\n"
            "    const template = await getTemplate(job.data.templateId);\n"
            "    await sendEmail(job.data.to, template, job.data.data);\n"
            "    return { sent: true };\n"
            "  },\n"
            "  { connection, concurrency: 10 },\n"
            ");\n"
            "\n"
            "emailWorker.on(\"failed\", (job, err) => {\n"
            "  logger.error({ jobId: job?.id, err: err.message }, \"Email job failed\");\n"
            "});\n"
            "\n"
            "process.on(\"SIGTERM\", async () => {\n"
            "  await emailWorker.close();\n"
            "  process.exit(0);\n"
            "});"
        ),
        "scheduler": (
            "import { Queue } from \"bullmq\";\n"
            "import IORedis from \"ioredis\";\n"
            "\n"
            "const connection = new IORedis(process.env.REDIS_URL!, {\n"
            "  maxRetriesPerRequest: null,\n"
            "});\n"
            "\n"
            "const maintenanceQueue = new Queue(\"maintenance\", { connection });\n"
            "\n"
            "// Recurring job: cleanup expired sessions every 6 hours\n"
            "await maintenanceQueue.add(\n"
            "  \"cleanup-sessions\",\n"
            "  { maxAgeHours: 72 },\n"
            "  { repeat: { cron: \"0 */6 * * *\" } },\n"
            ");\n"
            "\n"
            "// Recurring job: sync inventory every 5 minutes\n"
            "await maintenanceQueue.add(\n"
            "  \"sync-inventory\",\n"
            "  {},\n"
            "  { repeat: { every: 300_000 }, priority: 1 },\n"
            ");"
        ),
    },
    setup_code={
        "typescript": (
            "import { Queue, Worker } from \"bullmq\";\n"
            "import IORedis from \"ioredis\";\n"
            "\n"
            "const connection = new IORedis(process.env.REDIS_URL!, {\n"
            "  maxRetriesPerRequest: null,\n"
            "});\n"
            "\n"
            "export const taskQueue = new Queue(\"tasks\", {\n"
            "  connection,\n"
            "  defaultJobOptions: {\n"
            "    attempts: 3,\n"
            "    backoff: { type: \"exponential\", delay: 1000 },\n"
            "    removeOnComplete: { count: 1000 },\n"
            "    removeOnFail: { count: 5000 },\n"
            "  },\n"
            "});"
        ),
    },
)

register_service(BULLMQ)
