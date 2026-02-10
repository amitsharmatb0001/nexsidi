---
name: Next.js 14 (App Router)
description: Hybrid production standard using Next.js App Router, Route Groups, and Standalone Docker builds.
tags: [nextjs, react, typescript, frontend, fullstack]
---

# Next.js 14 (App Router) Template

## 1. Architectural Overview
- **Router**: App Router (RSC by default)
- **Organization**: Route Groups for domain separation `(marketing)`, `(dashboard)`
- **Deployment**: Standalone Mode (Minimal Docker Image)

## 2. Folder Structure
Strict separation of Routing Layer (`app/`) and UI Logic (`components/`).

```text
src/
├── app/                    # Routing Layer ONLY
│   ├── (marketing)/        # Route Group
│   │   ├── page.tsx
│   │   └── layout.tsx
│   ├── (dashboard)/
│   │   └── dashboard/
│   │       ├── page.tsx
│   │       └── loading.tsx
│   ├── api/                # Route Handlers
│   └── layout.tsx          # Root Layout
├── components/
│   ├── ui/                 # Shared Primitives
│   └── features/           # Reusable Feature Components
└── lib/                    # Singletons (DB, Auth)
```

## 3. Configuration & Rules

### next.config.mjs (Standalone)
```javascript
const nextConfig = {
  output: 'standalone', // CRITICAL for Docker size
  experimental: {
    optimizePackageImports: ['lucide-react', 'date-fns']
  }
};
export default nextConfig;
```

### Validation Command
```bash
npm run lint && tsc --noEmit
```

## 4. Docker Strategy (Standalone)
Requires manual copying of `.next/static` and `public/`.

```dockerfile
# Stage 1: Base
FROM node:22-alpine AS base
ENV PNPM_HOME="/pnpm" 
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable

# ... (Install Deps & Build) ...

# Stage 4: Runner
FROM node:22-alpine AS runner
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy Static Assets (CRITICAL)
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
```
