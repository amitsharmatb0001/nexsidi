"""Express framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Express + Drizzle ORM + Zod + TypeScript backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

EXPRESS_RULES: tuple[str, ...] = (
    "1. Use Drizzle ORM for database access — NEVER use Prisma, Sequelize, or TypeORM. "
    "Define schemas with `pgTable()` from `drizzle-orm/pg-core`",
    "2. Use Zod for request validation — NEVER use Joi, class-validator, or express-validator. "
    "Define schemas with `z.object({...})` and infer types with `z.infer<typeof schema>`",
    "3. Use Express Router for route grouping — "
    "`const router = Router()` then `router.get/post/put/delete`",
    "4. Use `express-async-errors` so async route handlers automatically catch errors — "
    "import it ONCE at app entry point, NEVER wrap handlers in try/catch for async errors",
    "5. Use `jose` library for JWT verification — NEVER use `jsonwebtoken`. "
    "Use `jwtVerify()` and `SignJWT` from `jose`",
    "6. Use `argon2` for password hashing — NEVER use bcrypt in Node.js. "
    "Use `argon2.hash()` and `argon2.verify()`",
    "7. Use ESM imports (`import`/`export`) — NEVER use CommonJS (`require`/`module.exports`). "
    "All files use `.ts` extension",
    "8. Use `export const` and `export type` — NEVER use `export default`. "
    "Named exports enable tree-shaking and better IDE support",
    "9. Drizzle queries: use `eq()`, `and()`, `or()` from `drizzle-orm` — "
    "NEVER write raw SQL strings. Use `db.select().from(table).where(eq(table.col, val))`",
    "10. HTTP status codes: use constants from a shared file or numeric literals — "
    "NEVER use magic strings for status descriptions",
    "11. Every route handler signature: `(req: Request, res: Response, next: NextFunction)` — "
    "call `next(error)` for error propagation to error middleware",
    "12. NEVER use 'any' type — use proper TypeScript types. "
    "NEVER use `// TODO`, `// FIXME`, or empty function bodies",
    "13. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use `@/` path alias for project-internal imports",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

EXPRESS_GOLDEN_EXAMPLES: dict[str, str] = {
    "db_schema": '''\
import { bigint, boolean, pgTable, text, timestamp, varchar } from "drizzle-orm/pg-core";
import { relations } from "drizzle-orm";

export const users = pgTable("users", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedAlwaysAsIdentity(),
  email: varchar("email", { length: 255 }).notNull().unique(),
  hashedPassword: text("hashed_password").notNull(),
  fullName: varchar("full_name", { length: 255 }).notNull(),
  isActive: boolean("is_active").notNull().default(true),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export const usersRelations = relations(users, ({ many }) => ({
  products: many(products),
}));

export const products = pgTable("products", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedAlwaysAsIdentity(),
  name: varchar("name", { length: 255 }).notNull(),
  description: text("description"),
  price: bigint("price", { mode: "number" }).notNull(),
  ownerId: bigint("owner_id", { mode: "number" }).notNull().references(() => users.id),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export const productsRelations = relations(products, ({ one }) => ({
  owner: one(users, { fields: [products.ownerId], references: [users.id] }),
}));
''',
    "types": '''\
import { z } from "zod";

export const CreateUserSchema = z.object({
  email: z.string().email(),
  fullName: z.string().min(1).max(255),
  password: z.string().min(8).max(128),
});

export type CreateUserInput = z.infer<typeof CreateUserSchema>;

export const LoginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

export type LoginInput = z.infer<typeof LoginSchema>;

export const CreateProductSchema = z.object({
  name: z.string().min(1).max(255),
  description: z.string().optional(),
  price: z.number().positive(),
});

export type CreateProductInput = z.infer<typeof CreateProductSchema>;

export type UserResponse = {
  id: number;
  email: string;
  fullName: string;
  isActive: boolean;
  createdAt: Date;
};
''',
    "routes": '''\
import { Router } from "express";
import type { Request, Response, NextFunction } from "express";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { users } from "@/db/schema";
import { CreateUserSchema } from "@/types";
import { hashPassword } from "@/middleware/auth";

export const userRouter = Router();

userRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  const parsed = CreateUserSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.flatten() });
    return;
  }

  const { email, fullName, password } = parsed.data;

  const existing = await db.select().from(users).where(eq(users.email, email)).limit(1);
  if (existing.length > 0) {
    res.status(409).json({ error: "Email already registered" });
    return;
  }

  const [user] = await db.insert(users).values({
    email,
    fullName,
    hashedPassword: await hashPassword(password),
  }).returning();

  res.status(201).json({ id: user.id, email: user.email, fullName: user.fullName });
});
''',
    "middleware": '''\
import type { Request, Response, NextFunction } from "express";
import { jwtVerify, SignJWT } from "jose";
import * as argon2 from "argon2";

const JWT_SECRET = new TextEncoder().encode(process.env.JWT_SECRET || "change-me");
const JWT_ISSUER = "nexsidi";
const JWT_EXPIRY = "24h";

export const hashPassword = async (password: string): Promise<string> => {
  return argon2.hash(password);
};

export const verifyPassword = async (hash: string, password: string): Promise<boolean> => {
  return argon2.verify(hash, password);
};

export const createToken = async (userId: number, email: string): Promise<string> => {
  return new SignJWT({ sub: String(userId), email })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setIssuer(JWT_ISSUER)
    .setExpirationTime(JWT_EXPIRY)
    .sign(JWT_SECRET);
};

export const requireAuth = async (req: Request, res: Response, next: NextFunction) => {
  const header = req.headers.authorization;
  if (!header?.startsWith("Bearer ")) {
    res.status(401).json({ error: "Missing authorization header" });
    return;
  }

  try {
    const token = header.slice(7);
    const { payload } = await jwtVerify(token, JWT_SECRET, { issuer: JWT_ISSUER });
    (req as Request & { userId: number }).userId = Number(payload.sub);
    next();
  } catch {
    res.status(401).json({ error: "Invalid or expired token" });
  }
};
''',
}

EXPRESS_FILE_STRUCTURE: dict[str, str] = {
    "db_schema": "backend/src/db/schema.ts",
    "types": "backend/src/types/index.ts",
    "middleware": "backend/src/middleware/auth.ts",
    "routes": "backend/src/routes/",
    "services": "backend/src/services/",
    "tests": "backend/src/__tests__/",
    "seed": "backend/src/scripts/seed.ts",
    "index": "backend/src/index.ts",
    "db_client": "backend/src/db/index.ts",
}


EXPRESS_CONFIG = FrameworkConfig(
    name="express",
    display_name="Express",
    language="typescript",
    code_block_lang="typescript",
    error_comment_prefix="//",
    file_structure=EXPRESS_FILE_STRUCTURE,
    rules=EXPRESS_RULES,
    golden_examples=EXPRESS_GOLDEN_EXAMPLES,
    # OCP-FIX: generation DAG moved here from shubham.py module-level dicts
    generation_order=(
        {"name": "db_schema", "path": "backend/src/db/schema.ts", "task_type": "general",
         "description": "Drizzle ORM schema from architecture contract tables"},
        {"name": "types", "path": "backend/src/types/index.ts", "task_type": "general",
         "description": "Zod schemas and TypeScript types matching DB schema"},
        {"name": "middleware", "path": "backend/src/middleware/auth.ts", "task_type": "auth_code",
         "description": "JWT auth middleware and helpers"},
        {"name": "routes", "path": "backend/src/routes/", "task_type": "general",
         "description": "Express route handlers using types and services"},
        {"name": "services", "path": "backend/src/services/", "task_type": "general",
         "description": "Business logic services called by routes"},
        {"name": "tests", "path": "backend/src/__tests__/", "task_type": "general",
         "description": "Jest tests for all endpoints and services"},
        {"name": "seed", "path": "backend/src/scripts/seed.ts", "task_type": "general",
         "description": "Database seed script for development"},
    ),
    dependency_graph={
        "db_schema": set(),
        "types": {"db_schema"},
        "middleware": {"db_schema", "types"},
        "routes": {"db_schema", "types", "middleware"},
        "services": {"db_schema", "types", "middleware"},
        "tests": {"db_schema", "types", "middleware", "routes", "services"},
        "seed": {"db_schema"},
    },
)

register_framework(EXPRESS_CONFIG)
