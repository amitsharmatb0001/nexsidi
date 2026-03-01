"""Next.js framework configuration for Aanya's frontend code generation.

Rules, golden examples, and file structure for generating production-grade
Next.js 15 App Router components with React 19, Server Components, Tailwind CSS,
and TypeScript strict mode.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

NEXTJS_RULES: tuple[str, ...] = (
    "1. Use Next.js 15 App Router with React 19 — NEVER use the Pages Router (`pages/` directory). "
    "All routes live under `src/app/` using folder-based routing. Use `layout.tsx` for shared layouts, "
    "`page.tsx` for route segments, and `loading.tsx` / `error.tsx` for Suspense boundaries",
    "2. Use TypeScript strict mode (`strict: true` in tsconfig) — "
    "NEVER use `any` type anywhere. Use proper interfaces, discriminated unions, and type guards. "
    "Enable `strictNullChecks`, `noImplicitAny`, and `strictPropertyInitialization`. "
    "Import shared types from `@/types` (auto-generated from the architecture contract)",
    "3. Use Server Components by default — NEVER add `'use client'` unless the component uses "
    "browser APIs, event handlers, `useState`, `useEffect`, or `useContext`. "
    "Data fetching should happen in Server Components using `async` functions, not `useEffect`",
    "4. Import API functions from `@/lib/api-client` (auto-generated) — "
    "NEVER hardcode API URLs or create duplicate fetch wrappers. "
    "For server-side data fetching use the API client directly in Server Components. "
    "For client-side mutations use Server Actions or the API client inside `'use client'` components",
    "5. Use Server Actions for form submissions and mutations — "
    "Define actions with `'use server'` in separate files under `@/actions/`. "
    "NEVER use API route handlers (`route.ts`) when a Server Action can accomplish the same task. "
    "Always validate inputs with zod schemas before processing",
    "6. Use Tailwind CSS utility classes for ALL styling — "
    "NEVER use inline `style={{}}` objects, CSS Modules, or styled-components. "
    "Use responsive prefixes (`sm:`, `md:`, `lg:`) with mobile-first breakpoints (375px, 768px, 1280px). "
    "Extract repeated patterns into reusable components, not `@apply` directives",
    "7. Use `next/link` for ALL internal navigation — NEVER use plain `<a>` tags for same-origin links. "
    "Use `next/image` for ALL images with explicit `width`, `height`, and `alt` attributes. "
    "Use `next/font` for font loading — NEVER import fonts via `<link>` or CSS `@import`",
    "8. Implement proper loading and error states — wrap async pages with `<Suspense>` and provide "
    "`loading.tsx` for route-level loading indicators. Use `error.tsx` (a `'use client'` component) "
    "for error boundaries. NEVER render empty states or leave components without feedback during data fetching",
    "9. Use React 19 hooks correctly — `useState` and `useReducer` for local state, "
    "`useContext` for dependency injection, `useOptimistic` for optimistic UI updates, "
    "`useFormStatus` for form submission states. NEVER use `useEffect` for data fetching — "
    "fetch in Server Components or use Server Actions instead",
    "10. Accessibility: use semantic HTML elements (`<nav>`, `<main>`, `<article>`, `<section>`, `<header>`, `<footer>`) — "
    "NEVER use `<div>` soup for structural layout. Add `aria-label` on interactive elements without visible text, "
    "`aria-live` on dynamic content regions, and ensure full keyboard navigation with `tabIndex` and `onKeyDown`",
    "11. Use `@/` path alias for all project-internal imports (configured in `tsconfig.json` paths) — "
    "NEVER use relative imports like `../../components/`. Organize: `@/components/`, `@/contexts/`, "
    "`@/lib/`, `@/types/`, `@/actions/`, `@/hooks/`. ALWAYS match import paths to the file structure exactly",
    "12. NEVER use `// TODO`, `// FIXME`, placeholder text, or empty function bodies — "
    "every function, event handler, and callback must have a REAL, COMPLETE implementation. "
    "NEVER invent import paths — use ONLY names from the architecture contract and previously generated code",
    "13. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later. "
    "Each component must be fully self-contained and functional. NEVER output partial code or code fragments "
    "with ellipsis (`...`) or comments like `// rest of implementation`",
)

NEXTJS_GOLDEN_EXAMPLES: dict[str, str] = {
    "auth_context": '''\
'use client';

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { apiClient } from '@/lib/api-client';
import type { User, AuthTokens, LoginRequest, RegisterRequest } from '@/types';

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

interface AuthContextValue extends AuthState {
  login: (credentials: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<AuthState>({
    user: null,
    token: null,
    isAuthenticated: false,
    isLoading: true,
  });

  useEffect(() => {
    const stored = localStorage.getItem('access_token');
    if (!stored) {
      setState((prev) => ({ ...prev, isLoading: false }));
      return;
    }
    try {
      const payload = JSON.parse(atob(stored.split('.')[1]));
      if (payload.exp * 1000 <= Date.now()) {
        localStorage.removeItem('access_token');
        setState((prev) => ({ ...prev, isLoading: false }));
        return;
      }
      apiClient.auth.me().then((user) => {
        setState({ user, token: stored, isAuthenticated: true, isLoading: false });
      }).catch(() => {
        localStorage.removeItem('access_token');
        setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
      });
    } catch {
      localStorage.removeItem('access_token');
      setState((prev) => ({ ...prev, isLoading: false }));
    }
  }, []);

  const login = useCallback(async (credentials: LoginRequest) => {
    const tokens: AuthTokens = await apiClient.auth.login(credentials);
    localStorage.setItem('access_token', tokens.accessToken);
    const user = await apiClient.auth.me();
    setState({ user, token: tokens.accessToken, isAuthenticated: true, isLoading: false });
    router.push('/dashboard');
  }, [router]);

  const register = useCallback(async (data: RegisterRequest) => {
    await apiClient.auth.register(data);
    await login({ email: data.email, password: data.password });
  }, [login]);

  const logout = useCallback(() => {
    localStorage.removeItem('access_token');
    setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
    router.push('/login');
  }, [router]);

  return (
    <AuthContext value={{ ...state, login, register, logout }}>
      {children}
    </AuthContext>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
''',
    "layout": '''\
'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import type { ReactNode } from 'react';

interface NavItem {
  label: string;
  href: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', href: '/dashboard', icon: 'grid' },
  { label: 'Products', href: '/dashboard/products', icon: 'box' },
  { label: 'Orders', href: '/dashboard/orders', icon: 'clipboard' },
  { label: 'Settings', href: '/dashboard/settings', icon: 'settings' },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((prev) => !prev);
  }, []);

  const closeSidebar = useCallback(() => {
    setSidebarOpen(false);
  }, []);

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside
        className={`fixed inset-y-0 left-0 z-30 w-64 transform bg-white shadow-lg transition-transform duration-200 ease-in-out md:relative md:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
        aria-label="Sidebar navigation"
      >
        <div className="flex h-16 items-center justify-between border-b px-4">
          <Link href="/" className="text-xl font-bold text-gray-900">
            App
          </Link>
          <button
            type="button"
            onClick={closeSidebar}
            className="rounded-md p-2 text-gray-500 hover:bg-gray-100 md:hidden"
            aria-label="Close sidebar"
          >
            <span className="text-lg">&times;</span>
          </button>
        </div>
        <nav className="mt-4 space-y-1 px-2" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={closeSidebar}
                className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-blue-50 text-blue-700'
                    : 'text-gray-700 hover:bg-gray-100 hover:text-gray-900'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                <span aria-hidden="true">{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/30 md:hidden"
          onClick={closeSidebar}
          onKeyDown={(e) => e.key === 'Escape' && closeSidebar()}
          role="button"
          tabIndex={-1}
          aria-label="Close sidebar overlay"
        />
      )}

      <div className="flex flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b bg-white px-4 shadow-sm">
          <button
            type="button"
            onClick={toggleSidebar}
            className="rounded-md p-2 text-gray-500 hover:bg-gray-100 md:hidden"
            aria-label="Open sidebar"
          >
            <span className="text-lg">&#9776;</span>
          </button>
          <div className="flex items-center gap-4">
            <span className="text-sm text-gray-600">{user?.email}</span>
            <button
              type="button"
              onClick={logout}
              className="rounded-md bg-gray-100 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-200"
            >
              Sign out
            </button>
          </div>
        </header>
        <main className="flex-1 p-4 md:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}
''',
    "pages": '''\
import { Suspense } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';
import type { Product } from '@/types';

async function ProductTable() {
  const products: Product[] = await apiClient.products.list();

  if (products.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 p-8 text-center">
        <p className="text-gray-500">No products found.</p>
        <Link
          href="/dashboard/products/new"
          className="mt-4 inline-block rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          Create your first product
        </Link>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border bg-white shadow-sm">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Name</th>
            <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Price</th>
            <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Status</th>
            <th scope="col" className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200">
          {products.map((product) => (
            <tr key={product.id} className="hover:bg-gray-50 transition-colors">
              <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                <Link href={`/dashboard/products/${product.id}`} className="hover:text-blue-600 hover:underline">
                  {product.name}
                </Link>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                ${product.price.toFixed(2)}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm">
                <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${
                  product.isActive ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'
                }`}>
                  {product.isActive ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                <Link
                  href={`/dashboard/products/${product.id}/edit`}
                  className="text-blue-600 hover:text-blue-800 hover:underline"
                >
                  Edit
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="animate-pulse space-y-3">
      <div className="h-10 rounded bg-gray-200" />
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="h-12 rounded bg-gray-100" />
      ))}
    </div>
  );
}

export default function ProductsPage() {
  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Products</h1>
        <Link
          href="/dashboard/products/new"
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          Add product
        </Link>
      </div>
      <Suspense fallback={<TableSkeleton />}>
        <ProductTable />
      </Suspense>
    </section>
  );
}
''',
    "app": '''\
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { AuthProvider } from '@/contexts/AuthContext';
import '@/styles/globals.css';

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
});

export const metadata: Metadata = {
  title: { default: 'App', template: '%s | App' },
  description: 'Production application built with Next.js 15',
  viewport: { width: 'device-width', initialScale: 1 },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-gray-50 font-sans text-gray-900 antialiased">
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
''',
}

NEXTJS_FILE_STRUCTURE: dict[str, str] = {
    "contexts": "frontend/src/contexts/",
    "components": "frontend/src/components/",
    "pages": "frontend/src/app/",
    "app": "frontend/src/app/layout.tsx",
    "lib": "frontend/src/lib/",
    "types": "frontend/src/types/",
    "actions": "frontend/src/actions/",
    "hooks": "frontend/src/hooks/",
    "styles": "frontend/src/styles/globals.css",
    "public": "frontend/public/",
}

NEXTJS_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_context",
        "path": "frontend/src/contexts/AuthContext.tsx",
        "description": "Authentication context provider with login, register, logout, token management, and auto-restore from localStorage",
    },
    {
        "name": "layout",
        "path": "frontend/src/components/Layout.tsx",
        "description": "Responsive shell layout with sidebar navigation, mobile hamburger menu, header with user info and sign-out, and content area",
    },
    {
        "name": "pages",
        "path": "frontend/src/app/",
        "description": "Page components for each route using Server Components for data fetching, Suspense for loading states, and Tailwind for styling",
    },
    {
        "name": "app",
        "path": "frontend/src/app/layout.tsx",
        "description": "Root layout.tsx that sets up HTML document, loads fonts with next/font, wraps children in AuthProvider, and applies global styles",
    },
)

NEXTJS_CONFIG = FrontendFrameworkConfig(
    name="nextjs",
    display_name="Next.js",
    language="typescript",
    code_block_lang="tsx",
    component_extension=".tsx",
    file_structure=NEXTJS_FILE_STRUCTURE,
    rules=NEXTJS_RULES,
    golden_examples=NEXTJS_GOLDEN_EXAMPLES,
    generation_order=NEXTJS_GENERATION_ORDER,
)

register_frontend_framework(NEXTJS_CONFIG)
