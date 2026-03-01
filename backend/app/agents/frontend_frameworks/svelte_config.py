"""SvelteKit frontend framework configuration for Aanya's code generation.

Rules, golden examples, file structure, and generation order for producing
production-grade SvelteKit + TypeScript + Tailwind CSS frontends.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

SVELTE_RULES: tuple[str, ...] = (
    "1. Use SvelteKit file-based routing — pages are `+page.svelte`, layouts are `+layout.svelte`, "
    "server-side data loading is `+page.server.ts` or `+page.ts`, and API endpoints are `+server.ts`. "
    "NEVER create custom routing or use SPA hash-based routing",
    "2. Use Svelte stores (`writable`, `readable`, `derived`) from `svelte/store` for shared state — "
    "NEVER use plain module-level variables for reactive cross-component state. "
    "Create custom stores with `writable` and expose controlled methods via closure pattern",
    "3. Use SvelteKit `load` functions in `+page.server.ts` for server-side data fetching — "
    "return typed data that the page receives via `export let data: PageData`. "
    "NEVER fetch data inside `onMount` when a `load` function can provide it",
    "4. Use SvelteKit form actions (`export const actions` in `+page.server.ts`) for mutations — "
    "use `enhance` from `$app/forms` for progressive enhancement. "
    "Return `fail()` for validation errors. NEVER use raw `fetch` POST from the client when a form action suffices",
    "5. Use `$:` reactive declarations for derived values and side effects — "
    "NEVER manually recalculate values in event handlers when `$:` can keep them in sync. "
    "Chain reactive statements: `$: total = price * quantity; $: formatted = fmt(total);`",
    "6. Use TypeScript with `<script lang=\"ts\">` in every Svelte component — "
    "NEVER use plain JavaScript `<script>` blocks. All props MUST be typed with `export let prop: Type`. "
    "Import types from `$lib/types` and use `import type` for type-only imports",
    "7. Use Tailwind CSS utility classes for all styling — "
    "NEVER use `<style>` blocks, inline styles, or CSS modules. "
    "Responsive: mobile-first with `sm:`, `md:`, `lg:` breakpoints (375px, 768px, 1280px)",
    "8. Use `$app/navigation` (`goto`, `invalidateAll`, `invalidate`) for programmatic navigation — "
    "NEVER use `window.location` or raw `history.pushState`. "
    "Use `$app/stores` (`page`, `navigating`) for accessing route params via `$page.params` and `$page.url`",
    "9. Accessibility: use semantic HTML (`<nav>`, `<main>`, `<section>`, `<article>`), ARIA labels, "
    "keyboard navigation (`on:keydown`), and proper heading hierarchy. "
    "Every interactive element MUST have an accessible name",
    "10. Use `{#if}`, `{#each}`, `{#await}` template blocks for conditional and list rendering — "
    "NEVER use ternary operators inside markup or `.map()` calls. "
    "Always provide a key expression in `{#each items as item (item.id)}`",
    "11. Use SvelteKit hooks (`hooks.server.ts`) for auth middleware — "
    "implement `handle` for session validation with `event.locals`, "
    "`handleError` for error logging, and `sequence()` from `@sveltejs/kit/hooks` to compose handlers",
    "12. NEVER use 'TODO', '...', placeholder text, or stub functions — "
    "every component, store, load function, and form action must have a REAL, COMPLETE implementation. "
    "NEVER invent import paths — use ONLY `$lib/` for `src/lib/`, `$app/` for SvelteKit modules",
    "13. Output ONLY the code file — no markdown fences, no explanations, "
    "no comments about what to add later. One file per generation step",
    "14. Use `on:click`, `on:submit|preventDefault`, `on:keydown` for event handling — "
    "use `bind:value` for two-way binding on inputs. "
    "Use `createEventDispatcher` for custom component events with typed payloads",
)

SVELTE_GOLDEN_EXAMPLES: dict[str, str] = {
    "page": '''\
<script lang="ts">
  import type { PageData } from './$types';
  import ProductCard from '$lib/components/ProductCard.svelte';

  export let data: PageData;

  let searchQuery = '';

  $: filteredProducts = data.products.filter((product) =>
    product.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  $: resultCount = filteredProducts.length;
</script>

<svelte:head>
  <title>Products | MyApp</title>
  <meta name="description" content="Browse our product catalog" />
</svelte:head>

<main class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
  <div class="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
    <h1 class="text-3xl font-bold tracking-tight text-gray-900">Products</h1>
    <div class="relative">
      <input
        type="search"
        bind:value={searchQuery}
        placeholder="Search products..."
        class="w-full rounded-lg border border-gray-300 px-4 py-2 pl-10 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 sm:w-72"
        aria-label="Search products"
      />
      <svg
        class="absolute left-3 top-2.5 h-5 w-5 text-gray-400"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
    </div>
  </div>

  <p class="mb-4 text-sm text-gray-500" aria-live="polite">
    {resultCount} {resultCount === 1 ? 'product' : 'products'} found
  </p>

  {#if filteredProducts.length === 0}
    <div class="flex flex-col items-center justify-center py-16 text-center">
      <p class="text-lg text-gray-500">No products found matching your search.</p>
      <button
        on:click={() => (searchQuery = '')}
        class="mt-4 text-blue-600 hover:text-blue-500 hover:underline"
      >
        Clear search
      </button>
    </div>
  {:else}
    <div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
      {#each filteredProducts as product (product.id)}
        <ProductCard
          {product}
          on:select={(e) => console.log('Selected', e.detail.id)}
        />
      {/each}
    </div>
  {/if}
</main>
''',
    "server": '''\
import type { PageServerLoad, Actions } from './$types';
import { fail, redirect } from '@sveltejs/kit';
import type { Product, CreateProductInput } from '$lib/types';

export const load: PageServerLoad = async ({ locals, url, fetch }) => {
  if (!locals.user) {
    throw redirect(303, '/login');
  }

  const page = Math.max(1, Number(url.searchParams.get('page') ?? '1'));
  const limit = Math.min(100, Math.max(1, Number(url.searchParams.get('limit') ?? '20')));

  const response = await fetch(`/api/products?page=${page}&limit=${limit}`, {
    headers: { Authorization: `Bearer ${locals.session.token}` },
  });

  if (!response.ok) {
    return { products: [] as Product[], pagination: { page, limit, total: 0, totalPages: 0 } };
  }

  const { data, total } = await response.json();

  return {
    products: data as Product[],
    pagination: {
      page,
      limit,
      total,
      totalPages: Math.ceil(total / limit),
    },
  };
};

export const actions: Actions = {
  create: async ({ request, locals, fetch }) => {
    if (!locals.user) {
      throw redirect(303, '/login');
    }

    const formData = await request.formData();
    const name = formData.get('name')?.toString().trim() ?? '';
    const description = formData.get('description')?.toString().trim() ?? '';
    const price = Number(formData.get('price'));

    const errors: Record<string, string> = {};

    if (!name || name.length < 2) {
      errors.name = 'Name must be at least 2 characters';
    }
    if (Number.isNaN(price) || price <= 0) {
      errors.price = 'Price must be a positive number';
    }
    if (!description || description.length < 10) {
      errors.description = 'Description must be at least 10 characters';
    }

    if (Object.keys(errors).length > 0) {
      return fail(400, { errors, values: { name, description, price } });
    }

    const response = await fetch('/api/products', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${locals.session.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ name, description, price }),
    });

    if (!response.ok) {
      const { error } = await response.json();
      return fail(response.status, { errors: { form: error ?? 'Failed to create product' }, values: { name, description, price } });
    }

    return { success: true };
  },

  delete: async ({ request, locals, fetch }) => {
    if (!locals.user) {
      throw redirect(303, '/login');
    }

    const formData = await request.formData();
    const productId = formData.get('productId')?.toString();

    if (!productId) {
      return fail(400, { errors: { productId: 'Product ID is required' } });
    }

    const response = await fetch(`/api/products/${productId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${locals.session.token}` },
    });

    if (!response.ok) {
      return fail(response.status, { errors: { form: 'Failed to delete product' } });
    }

    return { success: true };
  },
};
''',
    "store": '''\
import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';

export interface AuthUser {
  id: number;
  email: string;
  fullName: string;
  role: string;
}

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  error: string | null;
}

const TOKEN_KEY = 'auth_token';

function getInitialToken(): string | null {
  if (!browser) return null;
  return localStorage.getItem(TOKEN_KEY);
}

function createAuthStore() {
  const initialState: AuthState = {
    user: null,
    token: getInitialToken(),
    loading: false,
    error: null,
  };

  const { subscribe, set, update } = writable<AuthState>(initialState);

  return {
    subscribe,

    login: async (email: string, password: string): Promise<void> => {
      update((s) => ({ ...s, loading: true, error: null }));

      try {
        const response = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        });

        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.error ?? 'Login failed');
        }

        const { user, token } = await response.json();

        if (browser) {
          localStorage.setItem(TOKEN_KEY, token);
        }

        set({ user, token, loading: false, error: null });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Login failed';
        update((s) => ({ ...s, loading: false, error: message }));
        throw err;
      }
    },

    logout: (): void => {
      if (browser) {
        localStorage.removeItem(TOKEN_KEY);
      }
      set({ user: null, token: null, loading: false, error: null });
    },

    setUser: (user: AuthUser): void => {
      update((s) => ({ ...s, user }));
    },

    setLoading: (loading: boolean): void => {
      update((s) => ({ ...s, loading }));
    },

    clearError: (): void => {
      update((s) => ({ ...s, error: null }));
    },
  };
}

export const authStore = createAuthStore();

export const isAuthenticated = derived(authStore, ($auth) => $auth.token !== null);

export const currentUser = derived(authStore, ($auth) => $auth.user);

export const authLoading = derived(authStore, ($auth) => $auth.loading);

export const authError = derived(authStore, ($auth) => $auth.error);
''',
    "component": '''\
<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let product: {
    id: number;
    name: string;
    description: string | null;
    price: number;
    imageUrl?: string;
    createdAt: string;
  };

  export let variant: 'card' | 'list' = 'card';

  const dispatch = createEventDispatcher<{
    select: { id: number };
    addToCart: { id: number; quantity: number };
  }>();

  let quantity = 1;

  $: formattedPrice = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(product.price / 100);

  $: totalPrice = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format((product.price * quantity) / 100);

  function handleSelect(): void {
    dispatch('select', { id: product.id });
  }

  function handleAddToCart(): void {
    dispatch('addToCart', { id: product.id, quantity });
    quantity = 1;
  }

  function incrementQuantity(): void {
    if (quantity < 99) quantity += 1;
  }

  function decrementQuantity(): void {
    if (quantity > 1) quantity -= 1;
  }
</script>

<article
  class="flex rounded-lg border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md
    {variant === 'card' ? 'flex-col p-4' : 'flex-row items-center gap-4 p-3'}"
  role="article"
  aria-label="Product: {product.name}"
>
  {#if product.imageUrl}
    <div class={variant === 'card' ? 'mb-4' : 'flex-shrink-0'}>
      <img
        src={product.imageUrl}
        alt={product.name}
        class="rounded-md object-cover {variant === 'card' ? 'h-48 w-full' : 'h-16 w-16'}"
        loading="lazy"
      />
    </div>
  {/if}

  <div class={variant === 'card' ? 'flex-1' : 'min-w-0 flex-1'}>
    <button
      on:click={handleSelect}
      class="text-left hover:text-blue-600"
      aria-label="View details for {product.name}"
    >
      <h3 class="truncate text-lg font-semibold text-gray-900">{product.name}</h3>
    </button>

    {#if product.description}
      <p class="mt-1 line-clamp-2 text-sm text-gray-500">{product.description}</p>
    {:else}
      <p class="mt-1 text-sm italic text-gray-400">No description available.</p>
    {/if}

    <p class="mt-2 text-xl font-bold text-blue-600">{formattedPrice}</p>
  </div>

  <div class="mt-4 flex items-center gap-2 {variant === 'list' ? 'mt-0 ml-auto' : ''}">
    <div class="flex items-center rounded-md border border-gray-300" role="group" aria-label="Quantity selector">
      <button
        type="button"
        on:click={decrementQuantity}
        disabled={quantity <= 1}
        class="px-2 py-1 text-gray-600 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
        aria-label="Decrease quantity"
      >
        &minus;
      </button>
      <span class="min-w-[2rem] px-2 py-1 text-center text-sm font-medium" aria-live="polite">
        {quantity}
      </span>
      <button
        type="button"
        on:click={incrementQuantity}
        disabled={quantity >= 99}
        class="px-2 py-1 text-gray-600 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
        aria-label="Increase quantity"
      >
        +
      </button>
    </div>

    <button
      type="button"
      on:click={handleAddToCart}
      class="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
      aria-label="Add {product.name} to cart"
    >
      Add ({totalPrice})
    </button>
  </div>

  <time
    class="mt-3 block text-xs text-gray-400 {variant === 'list' ? 'mt-0 ml-4' : ''}"
    datetime={product.createdAt}
  >
    Added {new Date(product.createdAt).toLocaleDateString()}
  </time>
</article>
''',
}

SVELTE_FILE_STRUCTURE: dict[str, str] = {
    "auth_store": "frontend/src/lib/stores/auth.ts",
    "layout": "frontend/src/routes/+layout.svelte",
    "root_layout": "frontend/src/routes/+layout.svelte",
    "pages": "frontend/src/routes/",
    "components": "frontend/src/lib/components/",
    "stores": "frontend/src/lib/stores/",
    "server": "frontend/src/routes/+page.server.ts",
    "hooks": "frontend/src/hooks.server.ts",
    "types": "frontend/src/lib/types/index.ts",
    "api_client": "frontend/src/lib/api-client.ts",
    "config": "frontend/src/lib/config.ts",
    "app_css": "frontend/src/app.css",
    "svelte_config": "frontend/svelte.config.js",
    "vite_config": "frontend/vite.config.ts",
    "tailwind_config": "frontend/tailwind.config.ts",
}

SVELTE_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_store",
        "path": "frontend/src/lib/stores/auth.ts",
        "description": "Writable auth store with login, logout, token persistence, and derived isAuthenticated/currentUser stores",
    },
    {
        "name": "layout",
        "path": "frontend/src/routes/+layout.svelte",
        "description": "Root layout with responsive navigation bar, sidebar, auth guard, and slot for page content",
    },
    {
        "name": "pages",
        "path": "frontend/src/routes/",
        "description": "All page components (+page.svelte) and server load functions (+page.server.ts) from contract frontend.pages",
    },
    {
        "name": "root_layout",
        "path": "frontend/src/routes/+layout.svelte",
        "description": "Final root layout — generated LAST with real import paths and verified child route integration",
    },
)

SVELTE_CONFIG = FrontendFrameworkConfig(
    name="svelte",
    display_name="SvelteKit",
    language="typescript",
    code_block_lang="svelte",
    component_extension=".svelte",
    file_structure=SVELTE_FILE_STRUCTURE,
    rules=SVELTE_RULES,
    golden_examples=SVELTE_GOLDEN_EXAMPLES,
    generation_order=SVELTE_GENERATION_ORDER,
)

register_frontend_framework(SVELTE_CONFIG)
