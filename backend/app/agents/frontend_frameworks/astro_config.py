"""Astro frontend framework configuration for Aanya's code generation.

Rules, golden examples, and file structure for generating production-grade
Astro sites with Islands Architecture, content collections, and Tailwind CSS.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

# ── Rules ────────────────────────────────────────────────────────────

ASTRO_RULES: tuple[str, ...] = (
    "1. Use `.astro` single-file components with a fenced frontmatter block (`---`) at the top — "
    "ALL server-side TypeScript (imports, data fetching, prop destructuring) goes inside the `---` fences, "
    "template HTML goes below",
    "2. Use Islands Architecture — interactive components MUST use a `client:*` directive "
    "(`client:load`, `client:visible`, `client:idle`, `client:media`, `client:only`). "
    "Static `.astro` components render to zero JavaScript by default",
    "3. Use content collections with type-safe schemas — define collections in `src/content/config.ts` "
    "using `defineCollection()` and `z` from `astro:content`. Query with `getCollection()` and `getEntry()`",
    "4. Use TypeScript in all frontmatter blocks — NEVER use `any` types. "
    "Define prop interfaces with `interface Props { ... }` and destructure via `const { title, items } = Astro.props`",
    "5. Use Tailwind CSS for all styling — NEVER use inline `style` attributes, CSS modules, or "
    "`<style>` blocks unless scoped component styles are strictly required. "
    "Responsive: mobile-first with `sm:`, `md:`, `lg:` breakpoints",
    "6. Use `<slot />` for component composition — named slots use `<slot name=\"header\" />` "
    "and are filled with `<Fragment slot=\"header\">`. NEVER pass children as props",
    "7. Use `Astro.props` for type-safe prop access — destructure at the top of the frontmatter block. "
    "NEVER access props via `this.props` or function parameters",
    "8. Use file-based routing in `src/pages/` — dynamic routes use `[param].astro` or `[...slug].astro`. "
    "Use `getStaticPaths()` for static generation of dynamic routes",
    "9. Use `<Image />` from `astro:assets` for optimized images — NEVER use raw `<img>` tags. "
    "Provide `width`, `height`, and `alt` attributes for all images",
    "10. Use environment variables via `import.meta.env` — public variables MUST be prefixed with `PUBLIC_`. "
    "NEVER expose server-only secrets to client-side island components",
    "11. Accessibility: semantic HTML (`<nav>`, `<main>`, `<article>`, `<section>`), "
    "ARIA labels on interactive elements, keyboard navigation support, "
    "skip-to-content links in layouts",
    "12. NEVER use `TODO`, placeholder text, empty function bodies, or stub components — "
    "EVERY component must be fully functional with real content and logic",
    "13. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use `@/` or `~/` path aliases for project-internal imports (configured in tsconfig.json)",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

# ── Golden examples ──────────────────────────────────────────────────

ASTRO_GOLDEN_EXAMPLES: dict[str, str] = {
    "layout": """\
---
import Header from "../components/Header.astro";
import Footer from "../components/Footer.astro";
import "@/styles/global.css";

interface Props {
  title: string;
  description?: string;
}

const { title, description = "Built with Astro" } = Astro.props;
---

<!doctype html>
<html lang="en" class="scroll-smooth">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="description" content={description} />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <title>{title}</title>
  </head>
  <body class="min-h-screen bg-white text-gray-900 antialiased dark:bg-gray-950 dark:text-gray-100">
    <a
      href="#main-content"
      class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-blue-600 focus:px-4 focus:py-2 focus:text-white"
    >
      Skip to content
    </a>
    <Header />
    <main id="main-content" class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <slot />
    </main>
    <Footer />
  </body>
</html>
""",
    "page": """\
---
import Layout from "../layouts/Layout.astro";
import ProductCard from "../components/ProductCard.astro";
import SearchBar from "../components/SearchBar";
import type { Product } from "@/types";

const response = await fetch(`${import.meta.env.PUBLIC_API_URL}/api/products`);
const { data: products }: { data: Product[] } = await response.json();

const title = "Products";
const description = "Browse our full catalog of products.";
---

<Layout title={title} description={description}>
  <section class="space-y-8">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <h1 class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white">
        {title}
      </h1>
      <SearchBar client:load placeholder="Search products..." />
    </div>

    {products.length === 0 ? (
      <div class="rounded-lg border border-dashed border-gray-300 p-12 text-center dark:border-gray-700">
        <p class="text-lg text-gray-500 dark:text-gray-400">
          No products found. Check back soon!
        </p>
      </div>
    ) : (
      <div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {products.map((product) => (
          <ProductCard
            name={product.name}
            description={product.description}
            price={product.price}
            href={`/products/${product.id}`}
          />
        ))}
      </div>
    )}
  </section>
</Layout>
""",
    "component": """\
---
interface Props {
  name: string;
  description: string | null;
  price: number;
  href: string;
}

const { name, description, price, href } = Astro.props;

const formattedPrice = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
}).format(price / 100);
---

<a
  href={href}
  class="group block rounded-xl border border-gray-200 bg-white p-6 shadow-sm transition-all hover:shadow-md hover:border-blue-300 dark:border-gray-700 dark:bg-gray-800 dark:hover:border-blue-600"
>
  <div class="space-y-3">
    <h3 class="text-lg font-semibold text-gray-900 group-hover:text-blue-600 dark:text-white dark:group-hover:text-blue-400">
      {name}
    </h3>
    {description && (
      <p class="line-clamp-2 text-sm text-gray-600 dark:text-gray-400">
        {description}
      </p>
    )}
    <div class="flex items-center justify-between pt-2">
      <span class="text-xl font-bold text-blue-600 dark:text-blue-400">
        {formattedPrice}
      </span>
      <span class="text-sm font-medium text-gray-500 group-hover:text-blue-600 dark:text-gray-400 dark:group-hover:text-blue-400">
        View details &rarr;
      </span>
    </div>
  </div>
</a>
""",
    "island": """\
import { useState, useEffect, useCallback } from "react";
import type { Product } from "@/types";

interface SearchBarProps {
  placeholder?: string;
}

export default function SearchBar({ placeholder = "Search..." }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  const search = useCallback(async (searchQuery: string) => {
    if (searchQuery.length < 2) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    setIsLoading(true);
    try {
      const response = await fetch(
        `${import.meta.env.PUBLIC_API_URL}/api/products?search=${encodeURIComponent(searchQuery)}`
      );
      const { data }: { data: Product[] } = await response.json();
      setResults(data);
      setIsOpen(data.length > 0);
    } catch (error) {
      console.error("Search failed:", error);
      setResults([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => search(query), 300);
    return () => clearTimeout(timer);
  }, [query, search]);

  return (
    <div className="relative w-full max-w-md">
      <div className="relative">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          aria-label="Search products"
          aria-expanded={isOpen}
          aria-controls="search-results"
          className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2.5 pl-10 text-sm text-gray-900 placeholder-gray-500 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-gray-600 dark:bg-gray-800 dark:text-white dark:placeholder-gray-400"
        />
        <svg
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={2}
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
          />
        </svg>
        {isLoading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
          </div>
        )}
      </div>

      {isOpen && (
        <ul
          id="search-results"
          role="listbox"
          className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-gray-800"
        >
          {results.map((product) => (
            <li key={product.id} role="option" aria-selected={false}>
              <a
                href={`/products/${product.id}`}
                className="flex items-center justify-between px-4 py-3 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                <span className="font-medium text-gray-900 dark:text-white">
                  {product.name}
                </span>
                <span className="text-gray-500 dark:text-gray-400">
                  {new Intl.NumberFormat("en-US", {
                    style: "currency",
                    currency: "USD",
                  }).format(product.price / 100)}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
""",
}

# ── File structure ───────────────────────────────────────────────────

ASTRO_FILE_STRUCTURE: dict[str, str] = {
    "layout": "frontend/src/layouts/Layout.astro",
    "pages": "frontend/src/pages/",
    "components": "frontend/src/components/",
    "islands": "frontend/src/components/islands/",
    "content_config": "frontend/src/content/config.ts",
    "types": "frontend/src/types/index.ts",
    "styles": "frontend/src/styles/global.css",
    "public": "frontend/public/",
    "config": "frontend/astro.config.mjs",
}

# ── Generation order ─────────────────────────────────────────────────

ASTRO_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "layout",
        "path": "frontend/src/layouts/Layout.astro",
        "description": "Base HTML layout with head, header, footer, skip-to-content link, and <slot /> for page content",
    },
    {
        "name": "pages",
        "path": "frontend/src/pages/",
        "description": "All page components from contract frontend.pages — file-based routing with data fetching in frontmatter",
    },
    {
        "name": "components",
        "path": "frontend/src/components/",
        "description": "Static .astro UI components (cards, headers, footers, navbars) — zero client-side JavaScript",
    },
    {
        "name": "islands",
        "path": "frontend/src/components/islands/",
        "description": "Interactive island components (React/Svelte/Vue) hydrated with client:load or client:visible directives",
    },
)

# ── Config & registration ────────────────────────────────────────────

ASTRO_CONFIG = FrontendFrameworkConfig(
    name="astro",
    display_name="Astro",
    language="typescript",
    code_block_lang="astro",
    component_extension=".astro",
    file_structure=ASTRO_FILE_STRUCTURE,
    rules=ASTRO_RULES,
    golden_examples=ASTRO_GOLDEN_EXAMPLES,
    generation_order=ASTRO_GENERATION_ORDER,
)

register_frontend_framework(ASTRO_CONFIG)
