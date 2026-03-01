"""Next.js framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Next.js App Router + Prisma + Zod + TypeScript backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

NEXTJS_RULES: tuple[str, ...] = (
    "1. Use Next.js App Router route handlers (GET, POST, PUT, DELETE) — NEVER use Pages Router API routes. "
    "Export async functions named after HTTP methods from `route.ts` files",
    "2. Use `NextResponse.json()` for all responses — NEVER use `res.json()` or `res.send()`. "
    "Import from `next/server`. Return `NextResponse.json(data, { status: 200 })`",
    "3. Use Prisma Client for database access — NEVER use Drizzle, Sequelize, or TypeORM. "
    "Use a singleton pattern: `import { prisma } from '@/lib/prisma'`",
    "4. Use Zod for request validation — NEVER use Joi, class-validator, or yup. "
    "Define schemas with `z.object({...})` and infer types with `z.infer<typeof schema>`",
    "5. Use Next.js middleware (`middleware.ts` at project root) for auth guards — "
    "export a `middleware` function and a `config` with `matcher` array for protected routes",
    "6. Use `jose` library for JWT verification in middleware — NEVER use `jsonwebtoken`. "
    "Use `jwtVerify()` and `SignJWT` from `jose`",
    "7. Use `argon2` for password hashing — NEVER use bcrypt. "
    "Use `argon2.hash()` and `argon2.verify()` in server-side route handlers only",
    "8. Use ESM imports (`import`/`export`) — NEVER use CommonJS (`require`/`module.exports`). "
    "All files use `.ts` or `.tsx` extension",
    "9. Use `export const` and `export type` — NEVER use `export default` except for React components. "
    "Named exports enable tree-shaking and better IDE support",
    "10. Prisma queries: use the Prisma Client query API — "
    "NEVER write raw SQL strings. Use `prisma.user.findMany({ where: { ... }, include: { ... } })`",
    "11. Use server actions with `'use server'` directive for form mutations — "
    "NEVER call route handlers from Server Components when a server action suffices",
    "12. NEVER use 'any' type — use proper TypeScript types. "
    "NEVER use `// TODO`, `// FIXME`, or empty function bodies",
    "13. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use `@/` path alias for project-internal imports (configured in tsconfig.json)",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

NEXTJS_GOLDEN_EXAMPLES: dict[str, str] = {
    "db_schema": '''\
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id             Int       @id @default(autoincrement())
  email          String    @unique
  hashedPassword String    @map("hashed_password")
  fullName       String    @map("full_name") @db.VarChar(255)
  isActive       Boolean   @default(true) @map("is_active")
  createdAt      DateTime  @default(now()) @map("created_at") @db.Timestamptz
  updatedAt      DateTime  @updatedAt @map("updated_at") @db.Timestamptz
  products       Product[]

  @@map("users")
}

model Product {
  id          Int      @id @default(autoincrement())
  name        String   @db.VarChar(255)
  description String?
  price       Int
  ownerId     Int      @map("owner_id")
  owner       User     @relation(fields: [ownerId], references: [id], onDelete: Cascade)
  createdAt   DateTime @default(now()) @map("created_at") @db.Timestamptz
  updatedAt   DateTime @updatedAt @map("updated_at") @db.Timestamptz

  @@index([ownerId])
  @@map("products")
}
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

export const UpdateProductSchema = CreateProductSchema.partial();

export type UpdateProductInput = z.infer<typeof UpdateProductSchema>;

export const IdParamSchema = z.object({
  id: z.coerce.number().int().positive(),
});

export type UserResponse = {
  id: number;
  email: string;
  fullName: string;
  isActive: boolean;
  createdAt: Date;
};
''',
    "routes": '''\
import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { CreateUserSchema } from "@/types";
import { hashPassword } from "@/lib/auth";

export const GET = async (request: NextRequest): Promise<NextResponse> => {
  const { searchParams } = request.nextUrl;
  const page = Math.max(1, Number(searchParams.get("page") ?? "1"));
  const limit = Math.min(100, Math.max(1, Number(searchParams.get("limit") ?? "20")));
  const skip = (page - 1) * limit;

  const [users, total] = await Promise.all([
    prisma.user.findMany({
      select: { id: true, email: true, fullName: true, isActive: true, createdAt: true },
      skip,
      take: limit,
      orderBy: { createdAt: "desc" },
    }),
    prisma.user.count(),
  ]);

  return NextResponse.json({ data: users, total, page, limit });
};

export const POST = async (request: NextRequest): Promise<NextResponse> => {
  const body: unknown = await request.json();
  const parsed = CreateUserSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  const { email, fullName, password } = parsed.data;

  const existing = await prisma.user.findUnique({ where: { email } });
  if (existing) {
    return NextResponse.json({ error: "Email already registered" }, { status: 409 });
  }

  const user = await prisma.user.create({
    data: {
      email,
      fullName,
      hashedPassword: await hashPassword(password),
    },
    select: { id: true, email: true, fullName: true, createdAt: true },
  });

  return NextResponse.json(user, { status: 201 });
};
''',
    "middleware": '''\
import { NextRequest, NextResponse } from "next/server";
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

export const verifyToken = async (token: string): Promise<{ sub: string; email: string }> => {
  const { payload } = await jwtVerify(token, JWT_SECRET, { issuer: JWT_ISSUER });
  return { sub: payload.sub as string, email: payload.email as string };
};

export const middleware = async (request: NextRequest): Promise<NextResponse> => {
  const header = request.headers.get("authorization");
  if (!header?.startsWith("Bearer ")) {
    return NextResponse.json({ error: "Missing authorization header" }, { status: 401 });
  }

  try {
    const token = header.slice(7);
    const { sub, email } = await verifyToken(token);
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-user-id", sub);
    requestHeaders.set("x-user-email", email);
    return NextResponse.next({ request: { headers: requestHeaders } });
  } catch {
    return NextResponse.json({ error: "Invalid or expired token" }, { status: 401 });
  }
};

export const config = {
  matcher: ["/api/users/:path*", "/api/products/:path*"],
};
''',
}

NEXTJS_FILE_STRUCTURE: dict[str, str] = {
    "db_schema": "backend/prisma/schema.prisma",
    "types": "backend/src/types/index.ts",
    "middleware": "backend/src/middleware.ts",
    "routes": "backend/src/app/api/",
    "lib": "backend/src/lib/",
    "db_client": "backend/src/lib/prisma.ts",
    "auth": "backend/src/lib/auth.ts",
    "services": "backend/src/services/",
    "tests": "backend/src/__tests__/",
    "seed": "backend/prisma/seed.ts",
}


NEXTJS_CONFIG = FrameworkConfig(
    name="nextjs",
    display_name="Next.js",
    language="typescript",
    code_block_lang="typescript",
    error_comment_prefix="//",
    file_structure=NEXTJS_FILE_STRUCTURE,
    rules=NEXTJS_RULES,
    golden_examples=NEXTJS_GOLDEN_EXAMPLES,
)

register_framework(NEXTJS_CONFIG)
