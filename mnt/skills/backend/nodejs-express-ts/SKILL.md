---
name: Node.js (Express + TypeScript)
description: The "Golden Standard" for Node.js backend services, featuring Native ESM, Biome linting, and a Feature-Based Architecture.
tags: [node, express, typescript, esm, backend]
---

# Node.js (Express + TypeScript) Template

## 1. Architectural Overview
This blueprint enforces specific constraints to minimize hallucination and maximize compile-ability for AI agents.
- **Paradigm**: Feature-Based Architecture (Vertical Slices)
- **Module System**: Native ESM (`"type": "module"`)
- **Validation**: Biome (Linting/Formatting) + `tsc` (Type Checking)
- **Runtime**: Node.js 22+

## 2. Folder Structure
The structure emphasizes co-location of concerns.

```text
src/
├── app.ts                  # App factory (middleware, setup)
├── server.ts               # Entry point (server start)
├── config/
│   ├── env.ts              # Zod environment validation
│   └── logger.ts           # Pino configuration
├── common/
│   ├── middleware/         # Global middleware
│   ├── utils/
│   └── types/
└── modules/                # Feature Slices
    ├── health/
    │   ├── health.controller.ts
    │   ├── health.routes.ts
    │   └── health.service.ts
    └── users/
        ├── users.controller.ts
        ├── users.routes.ts
        ├── users.service.ts
        ├── users.repository.ts
        ├── users.dto.ts    # Zod schemas
        └── users.test.ts
```

## 3. Configuration & Rules

### tsconfig.json (Strict ESM)
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "verbatimModuleSyntax": true,
    "noUncheckedIndexedAccess": true,
    "outDir": "dist"
  },
  "include": ["src/**/*"]
}
```

### package.json (Scripts)
```json
{
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/server.ts",
    "build": "tsc && tsc-alias",
    "start": "node dist/server.js",
    "lint": "biome check src",
    "type-check": "tsc --noEmit"
  }
}
```

### Validation Command
Run this command to verify correctness:
```bash
pnpm run lint && pnpm run type-check
```

## 4. Docker Strategy
- **Base Image**: `node:22-alpine`
- **Security**: Run as non-root user (UID 1001).
- **Optimization**: Multi-stage build (deps -> builder -> runner).

```dockerfile
# Stage 1: Base
FROM node:22-alpine AS base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app

# Stage 2: Deps
FROM base AS deps
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Stage 3: Builder
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm run build

# Stage 4: Runner
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 expressjs
COPY --from=builder /app/dist ./dist
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --prod --frozen-lockfile
RUN chown -R expressjs:nodejs /app
USER expressjs
EXPOSE 3000
CMD ["node", "dist/server.js"]
```
