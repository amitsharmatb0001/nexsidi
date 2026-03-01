"""Remix framework configuration for Aanya's frontend code generation.

Rules, golden examples, file structure, and generation order for producing
production-grade Remix + React Router v7 + TypeScript + Tailwind CSS frontends.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

# ── Rules ────────────────────────────────────────────────────────────

REMIX_RULES: tuple[str, ...] = (
    "1. Use Remix v2 with React Router v7 file-based routing — "
    "route modules live in `app/routes/` and export `loader`, `action`, and a default component",
    "2. Use `loader` functions for ALL data fetching — NEVER fetch data inside components with useEffect. "
    "Loaders run on the server before rendering and return typed JSON via the `json()` utility",
    "3. Use `action` functions for ALL form mutations (POST, PUT, DELETE) — "
    "NEVER use API calls from the client for mutations. Actions receive FormData and return typed responses",
    "4. Use the Remix `<Form>` component for ALL form submissions — NEVER use `<form>` with `onSubmit` "
    "or `fetch`/`axios` calls. `<Form method=\"post\">` triggers the route action automatically",
    "5. Use `useLoaderData<typeof loader>()` to access loader data in components — "
    "NEVER pass loader data as props through multiple levels. Each route owns its data",
    "6. Use `useActionData<typeof action>()` to access action results — "
    "use it for form validation errors, success messages, and mutation feedback",
    "7. Use nested routes for layout composition — parent routes render `<Outlet />` for child content. "
    "Shared layouts (nav, sidebar) belong in parent route modules, not duplicated in every page",
    "8. Export an `ErrorBoundary` component from every route module — "
    "use `useRouteError()` and `isRouteErrorResponse()` to handle expected (4xx) and unexpected errors",
    "9. Use TypeScript strict mode — NEVER use `any` type. "
    "Type loader and action returns explicitly with `LoaderFunctionArgs` and `ActionFunctionArgs`",
    "10. Use Tailwind CSS for all styling — NEVER use inline styles, CSS modules, or styled-components. "
    "Responsive: mobile-first with 375px, 768px, 1280px breakpoints",
    "11. Use `redirect()` from `@remix-run/node` for post-mutation redirects — "
    "NEVER use `useNavigate()` after form submissions. Return `redirect('/path')` from actions",
    "12. Use `<Link to=\"/path\">` and `<NavLink>` from `@remix-run/react` for navigation — "
    "NEVER use `<a href>` for internal links. NavLink provides `isActive` / `isPending` states",
    "13. NEVER use `TODO`, `FIXME`, placeholder text, or stub functions — "
    "every component, loader, and action must be fully implemented with real logic",
    "14. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use `~/` path alias for app-internal imports (configured in tsconfig.json)",
    "15. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

# ── Golden Examples ──────────────────────────────────────────────────

REMIX_GOLDEN_EXAMPLES: dict[str, str] = {
    "route": '''\
import type { LoaderFunctionArgs, ActionFunctionArgs } from "@remix-run/node";
import { json, redirect } from "@remix-run/node";
import { useLoaderData, useActionData, Form, Link } from "@remix-run/react";
import { requireUserId } from "~/utils/auth.server";

interface Product {
  id: number;
  name: string;
  description: string | null;
  price: number;
  createdAt: string;
}

interface LoaderData {
  products: Product[];
  total: number;
  page: number;
}

interface ActionData {
  errors?: {
    name?: string;
    price?: string;
  };
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const userId = await requireUserId(request);
  const url = new URL(request.url);
  const page = Math.max(1, Number(url.searchParams.get("page") ?? "1"));
  const limit = 20;

  const response = await fetch(
    `${process.env.API_URL}/products?ownerId=${userId}&page=${page}&limit=${limit}`,
    {
      headers: { Authorization: `Bearer ${await getSessionToken(request)}` },
    }
  );

  if (!response.ok) {
    throw new Response("Failed to load products", { status: response.status });
  }

  const { data, total } = await response.json();

  return json<LoaderData>({ products: data, total, page });
};

export const action = async ({ request }: ActionFunctionArgs) => {
  const userId = await requireUserId(request);
  const formData = await request.formData();
  const intent = formData.get("intent");

  if (intent === "create") {
    const name = formData.get("name");
    const price = formData.get("price");

    const errors: ActionData["errors"] = {};
    if (typeof name !== "string" || name.trim().length === 0) {
      errors.name = "Product name is required";
    }
    if (typeof price !== "string" || isNaN(Number(price)) || Number(price) <= 0) {
      errors.price = "Price must be a positive number";
    }
    if (Object.keys(errors).length > 0) {
      return json<ActionData>({ errors }, { status: 400 });
    }

    const response = await fetch(`${process.env.API_URL}/products`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${await getSessionToken(request)}`,
      },
      body: JSON.stringify({
        name: (name as string).trim(),
        price: Number(price),
        ownerId: userId,
      }),
    });

    if (!response.ok) {
      throw new Response("Failed to create product", { status: response.status });
    }

    return redirect("/products");
  }

  if (intent === "delete") {
    const productId = formData.get("productId");

    const response = await fetch(`${process.env.API_URL}/products/${productId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${await getSessionToken(request)}` },
    });

    if (!response.ok) {
      throw new Response("Failed to delete product", { status: response.status });
    }

    return redirect("/products");
  }

  return json<ActionData>({}, { status: 400 });
};

export default function ProductsRoute() {
  const { products, total, page } = useLoaderData<typeof loader>();
  const actionData = useActionData<typeof action>();

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <div className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Products</h1>
        <span className="text-sm text-gray-500">{total} total</span>
      </div>

      <Form method="post" className="mb-8 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-4 text-lg font-semibold text-gray-800">Add Product</h2>
        <input type="hidden" name="intent" value="create" />
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="name" className="mb-1 block text-sm font-medium text-gray-700">
              Name
            </label>
            <input
              id="name"
              name="name"
              type="text"
              required
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              aria-describedby={actionData?.errors?.name ? "name-error" : undefined}
            />
            {actionData?.errors?.name && (
              <p id="name-error" className="mt-1 text-sm text-red-600" role="alert">
                {actionData.errors.name}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="price" className="mb-1 block text-sm font-medium text-gray-700">
              Price
            </label>
            <input
              id="price"
              name="price"
              type="number"
              step="0.01"
              min="0.01"
              required
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              aria-describedby={actionData?.errors?.price ? "price-error" : undefined}
            />
            {actionData?.errors?.price && (
              <p id="price-error" className="mt-1 text-sm text-red-600" role="alert">
                {actionData.errors.price}
              </p>
            )}
          </div>
        </div>
        <button
          type="submit"
          className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          Add Product
        </button>
      </Form>

      <ul className="space-y-3">
        {products.map((product) => (
          <li
            key={product.id}
            className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm"
          >
            <div>
              <Link
                to={`/products/${product.id}`}
                className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
              >
                {product.name}
              </Link>
              <p className="text-sm text-gray-500">
                ${(product.price / 100).toFixed(2)}
              </p>
            </div>
            <Form method="post">
              <input type="hidden" name="intent" value="delete" />
              <input type="hidden" name="productId" value={product.id} />
              <button
                type="submit"
                className="rounded-md px-3 py-1 text-sm text-red-600 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-500"
                aria-label={`Delete ${product.name}`}
              >
                Delete
              </button>
            </Form>
          </li>
        ))}
      </ul>

      {products.length === 0 && (
        <p className="py-12 text-center text-gray-500">
          No products yet. Add your first product above.
        </p>
      )}

      {total > 20 && (
        <nav className="mt-6 flex justify-center gap-2" aria-label="Pagination">
          {page > 1 && (
            <Link
              to={`?page=${page - 1}`}
              className="rounded-md border border-gray-300 px-3 py-1 text-sm hover:bg-gray-50"
            >
              Previous
            </Link>
          )}
          <span className="px-3 py-1 text-sm text-gray-600">Page {page}</span>
          {page * 20 < total && (
            <Link
              to={`?page=${page + 1}`}
              className="rounded-md border border-gray-300 px-3 py-1 text-sm hover:bg-gray-50"
            >
              Next
            </Link>
          )}
        </nav>
      )}
    </div>
  );
}

export function ErrorBoundary() {
  const error = useRouteError();

  if (isRouteErrorResponse(error)) {
    return (
      <div className="mx-auto max-w-lg px-4 py-16 text-center">
        <h1 className="text-2xl font-bold text-gray-900">{error.status}</h1>
        <p className="mt-2 text-gray-600">{error.statusText || "Something went wrong"}</p>
        <Link
          to="/products"
          className="mt-4 inline-block text-blue-600 hover:text-blue-800 hover:underline"
        >
          Try again
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg px-4 py-16 text-center">
      <h1 className="text-2xl font-bold text-red-600">Unexpected Error</h1>
      <p className="mt-2 text-gray-600">Something went wrong. Please try again later.</p>
      <Link
        to="/"
        className="mt-4 inline-block text-blue-600 hover:text-blue-800 hover:underline"
      >
        Go home
      </Link>
    </div>
  );
}
''',
    "layout": '''\
import type { LoaderFunctionArgs } from "@remix-run/node";
import { json } from "@remix-run/node";
import {
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  NavLink,
  useLoaderData,
  Form,
} from "@remix-run/react";
import { getUser } from "~/utils/auth.server";

import "~/tailwind.css";

interface LoaderData {
  user: { id: number; email: string; fullName: string } | null;
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const user = await getUser(request);
  return json<LoaderData>({ user });
};

export default function App() {
  const { user } = useLoaderData<typeof loader>();

  return (
    <html lang="en" className="h-full bg-gray-50">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body className="h-full">
        <div className="flex h-full">
          <aside className="hidden w-64 flex-shrink-0 border-r border-gray-200 bg-white md:block">
            <div className="flex h-full flex-col">
              <div className="flex h-16 items-center px-6">
                <span className="text-lg font-bold text-gray-900">App</span>
              </div>
              <nav className="flex-1 space-y-1 px-3 py-4" aria-label="Main navigation">
                <NavLink
                  to="/"
                  end
                  className={({ isActive }) =>
                    `flex items-center rounded-md px-3 py-2 text-sm font-medium ${
                      isActive
                        ? "bg-blue-50 text-blue-700"
                        : "text-gray-700 hover:bg-gray-100 hover:text-gray-900"
                    }`
                  }
                >
                  Dashboard
                </NavLink>
                <NavLink
                  to="/products"
                  className={({ isActive }) =>
                    `flex items-center rounded-md px-3 py-2 text-sm font-medium ${
                      isActive
                        ? "bg-blue-50 text-blue-700"
                        : "text-gray-700 hover:bg-gray-100 hover:text-gray-900"
                    }`
                  }
                >
                  Products
                </NavLink>
              </nav>
              {user && (
                <div className="border-t border-gray-200 p-4">
                  <div className="flex items-center justify-between">
                    <div className="truncate">
                      <p className="truncate text-sm font-medium text-gray-900">{user.fullName}</p>
                      <p className="truncate text-xs text-gray-500">{user.email}</p>
                    </div>
                    <Form method="post" action="/logout">
                      <button
                        type="submit"
                        className="rounded-md px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                      >
                        Logout
                      </button>
                    </Form>
                  </div>
                </div>
              )}
            </div>
          </aside>

          <div className="flex flex-1 flex-col overflow-hidden">
            <header className="flex h-16 items-center justify-between border-b border-gray-200 bg-white px-4 md:px-6">
              <button
                type="button"
                className="rounded-md p-2 text-gray-500 hover:bg-gray-100 md:hidden"
                aria-label="Open navigation menu"
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <div className="flex-1" />
              {!user && (
                <NavLink
                  to="/login"
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
                >
                  Sign In
                </NavLink>
              )}
            </header>

            <main className="flex-1 overflow-y-auto">
              <Outlet />
            </main>
          </div>
        </div>

        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export function ErrorBoundary() {
  return (
    <html lang="en" className="h-full bg-gray-50">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body className="flex h-full items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-red-600">Application Error</h1>
          <p className="mt-2 text-gray-600">Something went wrong. Please refresh the page.</p>
        </div>
        <Scripts />
      </body>
    </html>
  );
}
''',
    "auth": '''\
import { createCookieSessionStorage, redirect } from "@remix-run/node";

const SESSION_SECRET = process.env.SESSION_SECRET;
if (!SESSION_SECRET) {
  throw new Error("SESSION_SECRET environment variable is required");
}

const sessionStorage = createCookieSessionStorage({
  cookie: {
    name: "__session",
    httpOnly: true,
    maxAge: 60 * 60 * 24,
    path: "/",
    sameSite: "lax",
    secrets: [SESSION_SECRET],
    secure: process.env.NODE_ENV === "production",
  },
});

const USER_SESSION_KEY = "userId";
const TOKEN_SESSION_KEY = "token";

export const getSession = async (request: Request) => {
  const cookie = request.headers.get("Cookie");
  return sessionStorage.getSession(cookie);
};

export const getUserId = async (request: Request): Promise<number | undefined> => {
  const session = await getSession(request);
  const userId = session.get(USER_SESSION_KEY);
  return typeof userId === "number" ? userId : undefined;
};

export const getSessionToken = async (request: Request): Promise<string | undefined> => {
  const session = await getSession(request);
  return session.get(TOKEN_SESSION_KEY);
};

export const requireUserId = async (
  request: Request,
  redirectTo: string = "/login"
): Promise<number> => {
  const userId = await getUserId(request);
  if (!userId) {
    const url = new URL(request.url);
    const searchParams = new URLSearchParams([["redirectTo", url.pathname]]);
    throw redirect(`${redirectTo}?${searchParams}`);
  }
  return userId;
};

export const getUser = async (
  request: Request
): Promise<{ id: number; email: string; fullName: string } | null> => {
  const userId = await getUserId(request);
  if (!userId) return null;

  const token = await getSessionToken(request);
  if (!token) return null;

  try {
    const response = await fetch(`${process.env.API_URL}/users/${userId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (!response.ok) return null;

    const user = await response.json();
    return { id: user.id, email: user.email, fullName: user.fullName };
  } catch {
    return null;
  }
};

export const createUserSession = async ({
  request,
  userId,
  token,
  redirectTo,
}: {
  request: Request;
  userId: number;
  token: string;
  redirectTo: string;
}) => {
  const session = await getSession(request);
  session.set(USER_SESSION_KEY, userId);
  session.set(TOKEN_SESSION_KEY, token);
  return redirect(redirectTo, {
    headers: {
      "Set-Cookie": await sessionStorage.commitSession(session, {
        maxAge: 60 * 60 * 24,
      }),
    },
  });
};

export const logout = async (request: Request) => {
  const session = await getSession(request);
  return redirect("/login", {
    headers: {
      "Set-Cookie": await sessionStorage.destroySession(session),
    },
  });
};
''',
    "component": '''\
import { Link } from "@remix-run/react";

interface Column<T> {
  key: keyof T;
  header: string;
  render?: (value: T[keyof T], row: T) => React.ReactNode;
}

interface DataTableProps<T extends { id: number | string }> {
  columns: Column<T>[];
  data: T[];
  emptyMessage?: string;
  linkPrefix?: string;
}

export function DataTable<T extends { id: number | string }>({
  columns,
  data,
  emptyMessage = "No data found.",
  linkPrefix,
}: DataTableProps<T>) {
  if (data.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white py-12 text-center">
        <p className="text-gray-500">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {columns.map((col) => (
              <th
                key={String(col.key)}
                scope="col"
                className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200">
          {data.map((row) => (
            <tr key={row.id} className="hover:bg-gray-50">
              {columns.map((col, colIndex) => {
                const value = row[col.key];
                const rendered = col.render ? col.render(value, row) : String(value ?? "");

                return (
                  <td key={String(col.key)} className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                    {colIndex === 0 && linkPrefix ? (
                      <Link
                        to={`${linkPrefix}/${row.id}`}
                        className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                      >
                        {rendered}
                      </Link>
                    ) : (
                      rendered
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface PaginationProps {
  page: number;
  total: number;
  pageSize: number;
  baseUrl?: string;
}

export function Pagination({ page, total, pageSize, baseUrl = "" }: PaginationProps) {
  const totalPages = Math.ceil(total / pageSize);

  if (totalPages <= 1) return null;

  return (
    <nav className="mt-4 flex items-center justify-between" aria-label="Pagination">
      <p className="text-sm text-gray-600">
        Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, total)} of {total}
      </p>
      <div className="flex gap-2">
        {page > 1 && (
          <Link
            to={`${baseUrl}?page=${page - 1}`}
            className="rounded-md border border-gray-300 bg-white px-3 py-1 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Previous
          </Link>
        )}
        {page < totalPages && (
          <Link
            to={`${baseUrl}?page=${page + 1}`}
            className="rounded-md border border-gray-300 bg-white px-3 py-1 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Next
          </Link>
        )}
      </div>
    </nav>
  );
}
''',
}

# ── File Structure ───────────────────────────────────────────────────

REMIX_FILE_STRUCTURE: dict[str, str] = {
    "root_layout": "frontend/app/root.tsx",
    "routes": "frontend/app/routes/",
    "components": "frontend/app/components/",
    "auth_utils": "frontend/app/utils/auth.server.ts",
    "utils": "frontend/app/utils/",
    "styles": "frontend/app/tailwind.css",
    "entry_client": "frontend/app/entry.client.tsx",
    "entry_server": "frontend/app/entry.server.tsx",
    "config": "frontend/remix.config.js",
    "types": "frontend/app/types/index.ts",
}

# ── Generation Order ─────────────────────────────────────────────────

REMIX_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_utils",
        "path": "frontend/app/utils/auth.server.ts",
        "description": "Server-side authentication utilities (session storage, requireUserId, login/logout helpers)",
    },
    {
        "name": "root_layout",
        "path": "frontend/app/root.tsx",
        "description": "Root layout with <html>, <head>, <body>, sidebar navigation, <Outlet />, and root ErrorBoundary",
    },
    {
        "name": "routes",
        "path": "frontend/app/routes/",
        "description": "All route modules with loader/action/component from contract frontend.pages",
    },
    {
        "name": "error_boundary",
        "path": "frontend/app/routes/$.tsx",
        "description": "Catch-all splat route with ErrorBoundary for unmatched URLs (404 page)",
    },
)

# ── Config Registration ──────────────────────────────────────────────

REMIX_CONFIG = FrontendFrameworkConfig(
    name="remix",
    display_name="Remix",
    language="typescript",
    code_block_lang="typescript",
    component_extension=".tsx",
    file_structure=REMIX_FILE_STRUCTURE,
    rules=REMIX_RULES,
    golden_examples=REMIX_GOLDEN_EXAMPLES,
    generation_order=REMIX_GENERATION_ORDER,
)

register_frontend_framework(REMIX_CONFIG)
