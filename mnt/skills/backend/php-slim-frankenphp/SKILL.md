---
name: PHP (Slim 4 + FrankenPHP)
description: Modern PHP architecture using Slim 4, Domain-Driven Design Lite, and FrankenPHP Worker Mode.
tags: [php, slim, frankenphp, ddd, backend]
---

# PHP (Slim 4 + FrankenPHP) Template

## 1. Architectural Overview
Reimagines PHP as a high-performance application server using FrankenPHP (Caddy).
- **Paradigm**: Domain-Driven Design (DDD) Lite / Action-Domain-Responder (ADR)
- **Runtime**: FrankenPHP 8.4+ (Worker Mode)
- **Validation**: PHPStan (Level Max) + PHP-CS-Fixer

## 2. Folder Structure
Strict separation of HTTP Actions and Domain Logic.

```text
.
├── config/                 # Container, Routes, Middleware
├── public/                 # Entry point
│   └── index.php
├── src/
│   ├── Action/             # HTTP Controllers
│   │   └── User/
│   │       └── ListUsersAction.php
│   ├── Domain/             # Pure Business Logic
│   │   └── User/
│   │       ├── Repository/
│   │       ├── Service/
│   │       └── User.php
│   └── Support/
└── frankenphp/
    └── Caddyfile
```

## 3. Configuration & Rules

### composer.json (Key Scripts)
```json
{
  "scripts": {
    "start": "frankenphp run --config frankenphp/Caddyfile",
    "lint": "php-cs-fixer fix --dry-run --diff",
    "analyze": "phpstan analyse src tests --level=max",
    "check": ["@lint", "@analyze", "@test"]
  },
  "autoload": {
    "psr-4": { "App\\": "src/" }
  }
}
```

### Validation Command
```bash
composer run check
```

## 4. Docker Strategy
Uses FrankenPHP in **Worker Mode** for persistent application state.

```dockerfile
# Stage 1: Build & Exts
FROM dunglas/frankenphp:php8.4-alpine AS base
RUN install-php-extensions intl opcache pdo_mysql zip
ENV SERVER_NAME=:80
ENV SERVER_ROOT=/app/public
WORKDIR /app

# Stage 2: Vendor
FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --prefer-dist --optimize-autoloader

# Stage 3: Runner
FROM base AS runner
COPY --from=vendor /app/vendor /app/vendor
COPY . /app
RUN chown -R www-data:www-data /app
ENV FRANKENPHP_CONFIG="worker ./public/index.php"
EXPOSE 80
CMD ["frankenphp", "run", "--config", "/etc/caddy/Caddyfile"]
```
