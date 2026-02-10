---
name: AI-Driven API-First Design
description: Workflow for generating OpenAPI 3.1 specs via AI and compiling them into type-safe code for Backend (Pydantic) and Frontend (TypeScript).
tags: [openapi, pydantic, typescript, ai-workflow, api-design]
---

# AI-Driven API-First Design Template

## 1. Workflow Overview
Move from "Code-First" to "Contract-First" using AI to generate the spec.
1. **Design**: AI generates modular OpenAPI 3.1 YAML.
2. **Validate**: Spectral linting.
3. **Generate**: `datamodel-code-generator` (Python) + `openapi-typescript` (Frontend).

## 2. Directory Structure (Spec)
Modularize the YAML to support scalability.

```text
/spec
├── openapi.yaml            # Root Orchestrator
├── /paths
│   └── /orders
│       ├── create.yaml
│       └── list.yaml
└── /components
    ├── /schemas            # Data Models (Pydantic Source)
    │   ├── Order.yaml
    │   └── Error.yaml
    └── /securitySchemes
```

## 3. OpenAPI 3.1 Standards
The prompt to the AI must enforce:
- **Version**: 3.1.0 (JSON Schema 2020-12)
- **Nullability**: `type: ["string", "null"]` (NOT `nullable: true`)
- **Polymorphism**: `oneOf` with `discriminator`.

## 4. Code Generation Commands

### Backend (Pydantic v2)
Generates strict Pydantic models.
```bash
datamodel-codegen \
  --input openapi.yaml \
  --output src/models.py \
  --output-model-type pydantic_v2.BaseModel \
  --use-annotated \
  --use-field-description \
  --use-union-operator
```

### Frontend (TypeScript)
Generates immutable type definitions.
```bash
npx openapi-typescript ./openapi.yaml \
  -o ./src/types/api.d.ts \
  --immutable-types \
  --default-non-nullable \
  --export-type
```

## 5. Validation (Spectral)
Ensure the AI output is valid before generation.
Rule: `no-inline-schemas` (All schemas must be effectively named in `components`).
