"""React framework configuration for Aanya's frontend code generation.

Rules, golden examples, and file structure for generating production-grade
React 19 applications with React Router v7, Vite, Tailwind CSS, and TypeScript
strict mode.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

REACT_RULES: tuple[str, ...] = (
    "1. Use React 19 with React Router v7 and Vite — NEVER use Create React App, Next.js APIs, "
    "or any server-side rendering primitives. The application is a client-rendered SPA. "
    "All routing is handled by React Router v7 `createBrowserRouter` with `<RouterProvider>`",
    "2. Use TypeScript strict mode (`strict: true` in tsconfig) — "
    "NEVER use `any` type anywhere. Use proper interfaces, discriminated unions, and type guards. "
    "Enable `strictNullChecks`, `noImplicitAny`, and `strictPropertyInitialization`. "
    "Import shared types from `@/types` (auto-generated from the architecture contract)",
    "3. Use React Router v7 `<Link>` component for ALL internal navigation — "
    "NEVER use plain `<a>` tags for same-origin links. Use `useNavigate()` for programmatic "
    "navigation, `useParams()` for route parameters, and `useSearchParams()` for query strings. "
    "Define routes using `createBrowserRouter` with `lazy` loading for code splitting",
    "4. Import API functions from `@/lib/api-client` (auto-generated) — "
    "NEVER hardcode API URLs or create duplicate fetch wrappers. "
    "Use React Router loaders for route-level data fetching where possible. "
    "For mutations, use React Router actions or call API client functions directly from event handlers",
    "5. Use React 19 hooks correctly — `useState` for local state, `useReducer` for complex state, "
    "`useContext` for dependency injection, `useOptimistic` for optimistic UI updates, "
    "`useFormStatus` inside `<form action>` for submission indicators. "
    "NEVER use `useEffect` for data fetching — use React Router loaders or `useSuspenseQuery` instead",
    "6. Use Tailwind CSS utility classes for ALL styling — "
    "NEVER use inline `style={{}}` objects, CSS Modules, or styled-components. "
    "Use responsive prefixes (`sm:`, `md:`, `lg:`) with mobile-first breakpoints (375px, 768px, 1280px). "
    "Extract repeated patterns into reusable components, not `@apply` directives",
    "7. Use React Router v7 route configuration with lazy-loaded components — "
    "NEVER import page components eagerly at the top of the router file. "
    "Use `lazy: () => import('@/pages/Dashboard')` for every page route. "
    "Define an `errorElement` on the root route and use `loader`/`action` for data operations",
    "8. Implement proper loading and error states — use React Router's `useNavigation()` for "
    "global loading indicators, `<Suspense>` with `React.lazy` for component-level code splitting, "
    "and `useRouteError()` inside `errorElement` for error boundaries. "
    "NEVER render empty states or leave components without feedback during data fetching",
    "9. Use `@/` path alias for all project-internal imports (configured in `vite.config.ts` and `tsconfig.json`) — "
    "NEVER use relative imports like `../../components/`. Organize: `@/components/`, `@/contexts/`, "
    "`@/lib/`, `@/types/`, `@/hooks/`, `@/pages/`. ALWAYS match import paths to the file structure exactly",
    "10. Accessibility: use semantic HTML elements (`<nav>`, `<main>`, `<article>`, `<section>`, `<header>`, `<footer>`) — "
    "NEVER use `<div>` soup for structural layout. Add `aria-label` on interactive elements without visible text, "
    "`aria-live` on dynamic content regions, and ensure full keyboard navigation with `tabIndex` and `onKeyDown`",
    "11. Use `<img>` with explicit `width`, `height`, `alt`, and `loading=\"lazy\"` attributes — "
    "NEVER omit `alt` text or use `next/image` (this is not a Next.js project). "
    "For icons, use inline SVGs or an icon library imported as React components. "
    "NEVER use icon fonts loaded via external CDN links",
    "12. NEVER use `// TODO`, `// FIXME`, placeholder text, or empty function bodies — "
    "every function, event handler, and callback must have a REAL, COMPLETE implementation. "
    "NEVER invent import paths — use ONLY names from the architecture contract and previously generated code",
    "13. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later. "
    "Each component must be fully self-contained and functional. NEVER output partial code or code fragments "
    "with ellipsis (`...`) or comments like `// rest of implementation`",
)

REACT_GOLDEN_EXAMPLES: dict[str, str] = {
    "auth_context": '''\
import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
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
  const navigate = useNavigate();
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
    navigate('/dashboard');
  }, [navigate]);

  const register = useCallback(async (data: RegisterRequest) => {
    await apiClient.auth.register(data);
    await login({ email: data.email, password: data.password });
  }, [login]);

  const logout = useCallback(() => {
    localStorage.removeItem('access_token');
    setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
    navigate('/login');
  }, [navigate]);

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
    "router": '''\
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AppLayout } from '@/components/Layout';
import type { RouteObject } from 'react-router-dom';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('access_token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    if (payload.exp * 1000 <= Date.now()) {
      localStorage.removeItem('access_token');
      return <Navigate to="/login" replace />;
    }
  } catch {
    localStorage.removeItem('access_token');
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

const routes: RouteObject[] = [
  {
    path: '/',
    errorElement: (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <h1 className="text-4xl font-bold text-gray-900">Something went wrong</h1>
          <p className="mt-2 text-gray-600">An unexpected error occurred.</p>
          <a href="/" className="mt-4 inline-block text-blue-600 hover:underline">Go home</a>
        </div>
      </div>
    ),
    children: [
      {
        path: 'login',
        lazy: () => import('@/pages/Login').then((m) => ({ Component: m.default })),
      },
      {
        path: 'register',
        lazy: () => import('@/pages/Register').then((m) => ({ Component: m.default })),
      },
      {
        path: 'dashboard',
        element: (
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        ),
        children: [
          {
            index: true,
            lazy: () => import('@/pages/Dashboard').then((m) => ({ Component: m.default })),
          },
          {
            path: 'products',
            lazy: () => import('@/pages/Products').then((m) => ({ Component: m.default })),
          },
          {
            path: 'products/:id',
            lazy: () => import('@/pages/ProductDetail').then((m) => ({ Component: m.default })),
          },
          {
            path: 'settings',
            lazy: () => import('@/pages/Settings').then((m) => ({ Component: m.default })),
          },
        ],
      },
      {
        index: true,
        element: <Navigate to="/dashboard" replace />,
      },
      {
        path: '*',
        lazy: () => import('@/pages/NotFound').then((m) => ({ Component: m.default })),
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
''',
    "layout": '''\
import { useState, useCallback } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';

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

export function AppLayout() {
  const location = useLocation();
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
          <Link to="/" className="text-xl font-bold text-gray-900">
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
            const isActive = location.pathname === item.href || location.pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                to={item.href}
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
        <main className="flex-1 p-4 md:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
''',
    "pages": '''\
import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '@/lib/api-client';
import type { Product } from '@/types';

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.products.list();
      setProducts(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load products';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  if (loading) {
    return (
      <section>
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Products</h1>
        </div>
        <div className="animate-pulse space-y-3">
          <div className="h-10 rounded bg-gray-200" />
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="h-12 rounded bg-gray-100" />
          ))}
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section>
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Products</h1>
        </div>
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <p className="text-red-700">{error}</p>
          <button
            type="button"
            onClick={loadProducts}
            className="mt-4 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Products</h1>
        <Link
          to="/dashboard/products/new"
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          Add product
        </Link>
      </div>
      {products.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-300 p-8 text-center">
          <p className="text-gray-500">No products found.</p>
          <Link
            to="/dashboard/products/new"
            className="mt-4 inline-block rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Create your first product
          </Link>
        </div>
      ) : (
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
                    <Link to={`/dashboard/products/${product.id}`} className="hover:text-blue-600 hover:underline">
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
                    <Link to={`/dashboard/products/${product.id}/edit`} className="text-blue-600 hover:text-blue-800 hover:underline">
                      Edit
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
''',
    "app": '''\
import { RouterProvider } from 'react-router-dom';
import { AuthProvider } from '@/contexts/AuthContext';
import { router } from '@/router';

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
''',
}

REACT_FILE_STRUCTURE: dict[str, str] = {
    "contexts": "frontend/src/contexts/",
    "components": "frontend/src/components/",
    "pages": "frontend/src/pages/",
    "app": "frontend/src/App.tsx",
    "router": "frontend/src/router.tsx",
    "lib": "frontend/src/lib/",
    "types": "frontend/src/types/",
    "hooks": "frontend/src/hooks/",
    "styles": "frontend/src/index.css",
    "public": "frontend/public/",
}

REACT_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_context",
        "path": "frontend/src/contexts/AuthContext.tsx",
        "description": "Authentication context provider with login, register, logout, token management, and auto-restore from localStorage",
    },
    {
        "name": "router",
        "path": "frontend/src/router.tsx",
        "description": "React Router v7 configuration with createBrowserRouter, lazy-loaded routes, auth-protected routes, and error boundaries",
    },
    {
        "name": "layout",
        "path": "frontend/src/components/Layout.tsx",
        "description": "Responsive shell layout with sidebar navigation, mobile hamburger menu, header with user info and sign-out, and Outlet for child routes",
    },
    {
        "name": "pages",
        "path": "frontend/src/pages/",
        "description": "Page components for each route with data fetching, loading states, error handling, and Tailwind styling",
    },
    {
        "name": "app",
        "path": "frontend/src/App.tsx",
        "description": "Root App component that wraps AuthProvider around RouterProvider — generated LAST with real import paths",
    },
)

REACT_CONFIG = FrontendFrameworkConfig(
    name="react",
    display_name="React",
    language="typescript",
    code_block_lang="tsx",
    component_extension=".tsx",
    file_structure=REACT_FILE_STRUCTURE,
    rules=REACT_RULES,
    golden_examples=REACT_GOLDEN_EXAMPLES,
    generation_order=REACT_GENERATION_ORDER,
)

register_frontend_framework(REACT_CONFIG)
