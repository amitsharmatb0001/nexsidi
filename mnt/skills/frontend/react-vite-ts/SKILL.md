---
name: React (Vite + TypeScript)
description: Feature-based React architecture using Vite, preventing "Component Soup" and enabling high-speed compilation.
tags: [react, vite, typescript, frontend]
---

# React (Vite + TypeScript) Template

## 1. Architectural Overview
- **Structure**: Bulletproof React (Feature-Based)
- **Build Tool**: Vite (SWC)
- **Validation**: Biome + `tsc`

## 2. Folder Structure
Groups components by business feature rather than technical type.

```text
src/
├── app/                    # Providers, Global Routes
│   ├── routes.tsx
│   └── App.tsx
├── components/             # Shared "Dumb" UI Components
│   └── ui/                 # (Button, Input, etc.)
├── features/               # Smart Feature Modules
│   ├── auth/
│   │   ├── api/            # React Query hooks
│   │   ├── components/     # Feature-specific components
│   │   ├── routes/
│   │   └── types/
│   └── dashboard/
├── hooks/                  # Global hooks
└── lib/                    # Axios/QueryClient setup
```

## 3. Configuration & Rules

### vite.config.ts (Optimization)
```typescript
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  build: {
    target: 'esnext',
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (id.includes('node_modules')) {
             if (id.includes('react')) return 'vendor-react';
             return 'vendor';
          }
        }
      }
    }
  }
});
```

### Validation Command
```bash
npm run validate
# Defined as: "tsc --noEmit && biome check src"
```

## 4. Docker Strategy (Nginx)
Serving static assets via Nginx with robust caching headers.

```dockerfile
# Stage 1: Build
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Serve
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### nginx.conf (SPA Support)
Critical for routing to work:
```nginx
location / {
    try_files $uri $uri/ /index.html;
    add_header Cache-Control "no-cache, no-store, must-revalidate";
}
location ~* \.(?:css|js|jpg|svg)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```
