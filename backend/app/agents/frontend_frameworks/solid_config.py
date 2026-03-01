"""Solid.js frontend framework configuration.

Solid.js is a reactive JavaScript framework that uses fine-grained reactivity
via signals, stores, and effects -- no virtual DOM.  Components are plain
functions that run once; the reactive primitives handle updates.

Key choices:
- ``createSignal`` / ``createStore`` for state
- ``createResource`` for async data fetching
- ``Show`` / ``For`` / ``Switch`` control-flow components instead of ternaries
- ``@solidjs/router`` for client-side routing
- Tailwind CSS for styling
- TypeScript strict mode throughout
"""

from app.agents.frontend_frameworks import (
    FrontendFrameworkConfig,
    register_frontend_framework,
)

# ── Rules ────────────────────────────────────────────────────────────

_RULES: tuple[str, ...] = (
    # 1 - Reactivity primitives
    "Use `createSignal` for local reactive state. Signals return a "
    "`[getter, setter]` tuple -- always call the getter as a function "
    "(e.g. `count()`) to read the value.",

    # 2 - Effects
    "Use `createEffect` for side effects that depend on reactive values. "
    "Effects automatically track any signal read inside them and re-run "
    "when those signals change.  Never manually subscribe to signals.",

    # 3 - Async data
    "Use `createResource` to fetch async data.  It returns "
    "`[data, { mutate, refetch }]` and integrates with `<Suspense>` "
    "boundaries for loading states.  Avoid raw `fetch` inside effects.",

    # 4 - Store
    "Use `createStore` (from `solid-js/store`) for deeply nested or "
    "shared state objects.  Mutate stores with `setStore` using path "
    "syntax (e.g. `setStore('todos', index, 'done', true)`).",

    # 5 - Components
    "Components are plain TypeScript functions that execute exactly once. "
    "Return JSX that uses signals and stores for reactivity.  Do NOT "
    "treat the component body like a React render function -- there are "
    "no re-renders.",

    # 6 - Control flow
    "Use `<Show when={...}>`, `<For each={...}>`, and "
    "`<Switch>/<Match>` control-flow components instead of ternary "
    "expressions or `.map()` for conditional and list rendering.",

    # 7 - JSX / no virtual DOM
    "Solid compiles JSX to real DOM operations -- there is no virtual "
    "DOM diffing.  Avoid patterns that assume virtual DOM reconciliation "
    "(e.g. keyed re-renders, `React.memo` equivalents).",

    # 8 - TypeScript
    "Use TypeScript strict mode everywhere.  Define `Props` interfaces "
    "for every component.  Use generics for signals when the type "
    "cannot be inferred (e.g. `createSignal<User | null>(null)`).",

    # 9 - Routing
    "Use `@solidjs/router` with `<Router>`, `<Route>`, and `<A>` "
    "components.  Define routes declaratively and use `useParams`, "
    "`useNavigate`, and `useSearchParams` for navigation.",

    # 10 - Styling
    "Use Tailwind CSS utility classes for all styling.  Prefer "
    "`class={...}` over `classList` unless toggling multiple classes "
    "conditionally.  Do not use CSS-in-JS libraries.",

    # 11 - Project structure
    "Organise code into `components/`, `stores/`, `pages/`, and "
    "`routes/` directories under `frontend/src/`.  Keep store files "
    "co-located with the features they serve.",

    # 12 - Error boundaries
    "Wrap top-level routes with `<ErrorBoundary fallback={...}>` and "
    "provide user-friendly error UIs.  Combine with `<Suspense>` to "
    "handle both loading and error states gracefully.",

    # 13 - Derived state
    "Derive computed values by creating plain functions that read "
    "signals (e.g. `const doubled = () => count() * 2`).  Do NOT wrap "
    "derived values in `createSignal` -- Solid tracks them automatically.",

    # 14 - Lifecycle
    "Use `onMount` for one-time setup (e.g. event listeners, timers) "
    "and `onCleanup` inside effects or `onMount` to tear down resources. "
    "There is no `componentDidUpdate` equivalent -- effects handle it.",
)

# ── Golden Examples ──────────────────────────────────────────────────

_GOLDEN_EXAMPLES: dict[str, str] = {
    # ---- component: a counter with signals ----------------------------
    "component": '''\
import { createSignal, type Component } from "solid-js";

interface CounterProps {
  initial?: number;
}

const Counter: Component<CounterProps> = (props) => {
  const [count, setCount] = createSignal(props.initial ?? 0);

  const increment = () => setCount((prev) => prev + 1);
  const decrement = () => setCount((prev) => prev - 1);

  return (
    <div class="flex items-center gap-4 p-4">
      <button
        class="rounded bg-red-500 px-3 py-1 text-white hover:bg-red-600"
        onClick={decrement}
      >
        -
      </button>
      <span class="min-w-[3ch] text-center text-2xl font-bold">
        {count()}
      </span>
      <button
        class="rounded bg-green-500 px-3 py-1 text-white hover:bg-green-600"
        onClick={increment}
      >
        +
      </button>
    </div>
  );
};

export default Counter;
''',

    # ---- store: shared auth store with createStore --------------------
    "store": '''\
import { createStore } from "solid-js/store";
import { createEffect, createRoot } from "solid-js";

export interface User {
  id: string;
  email: string;
  name: string;
  avatarUrl: string | null;
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

function createAuthStore() {
  const [state, setState] = createStore<AuthState>({
    user: null,
    token: null,
    isAuthenticated: false,
    isLoading: true,
  });

  // Persist token to localStorage whenever it changes
  createEffect(() => {
    if (state.token) {
      localStorage.setItem("auth_token", state.token);
    } else {
      localStorage.removeItem("auth_token");
    }
  });

  const login = async (email: string, password: string): Promise<void> => {
    setState("isLoading", true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) throw new Error("Login failed");
      const data = await res.json();
      setState({
        user: data.user,
        token: data.token,
        isAuthenticated: true,
        isLoading: false,
      });
    } catch (err) {
      setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
      throw err;
    }
  };

  const logout = (): void => {
    setState({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
    });
  };

  const initialize = async (): Promise<void> => {
    const token = localStorage.getItem("auth_token");
    if (!token) {
      setState("isLoading", false);
      return;
    }
    try {
      const res = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error("Token expired");
      const user = await res.json();
      setState({ user, token, isAuthenticated: true, isLoading: false });
    } catch {
      setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
    }
  };

  return { state, login, logout, initialize } as const;
}

// Singleton created inside a reactive root so effects work at module scope.
export const authStore = createRoot(createAuthStore);
''',

    # ---- router: @solidjs/router setup --------------------------------
    "router": '''\
import { lazy, type Component } from "solid-js";
import { Router, Route } from "@solidjs/router";

import Layout from "./components/Layout";

const Home = lazy(() => import("./pages/Home"));
const Login = lazy(() => import("./pages/Login"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Settings = lazy(() => import("./pages/Settings"));
const NotFound = lazy(() => import("./pages/NotFound"));

const AppRouter: Component = () => {
  return (
    <Router root={Layout}>
      <Route path="/" component={Home} />
      <Route path="/login" component={Login} />
      <Route path="/dashboard" component={Dashboard} />
      <Route path="/settings" component={Settings} />
      <Route path="*404" component={NotFound} />
    </Router>
  );
};

export default AppRouter;
''',

    # ---- resource: createResource for API data ------------------------
    "resource": '''\
import { createResource, Show, For, type Component } from "solid-js";

interface Todo {
  id: number;
  title: string;
  completed: boolean;
  userId: number;
}

const fetchTodos = async (): Promise<Todo[]> => {
  const res = await fetch("/api/todos");
  if (!res.ok) throw new Error(`Failed to fetch todos: ${res.status}`);
  return res.json();
};

const TodoList: Component = () => {
  const [todos, { refetch }] = createResource<Todo[]>(fetchTodos);

  return (
    <div class="mx-auto max-w-lg p-6">
      <div class="mb-4 flex items-center justify-between">
        <h1 class="text-2xl font-bold">Todos</h1>
        <button
          class="rounded bg-blue-500 px-3 py-1 text-sm text-white hover:bg-blue-600"
          onClick={refetch}
        >
          Refresh
        </button>
      </div>

      <Show when={!todos.loading} fallback={<p class="text-gray-500">Loading...</p>}>
        <Show
          when={!todos.error}
          fallback={
            <p class="text-red-500">
              Error: {(todos.error as Error).message}
            </p>
          }
        >
          <ul class="space-y-2">
            <For each={todos()}>
              {(todo) => (
                <li
                  class="flex items-center gap-2 rounded border p-3"
                  classList={{ "opacity-50 line-through": todo.completed }}
                >
                  <span
                    class={`h-3 w-3 rounded-full ${
                      todo.completed ? "bg-green-500" : "bg-yellow-400"
                    }`}
                  />
                  <span>{todo.title}</span>
                </li>
              )}
            </For>
          </ul>
        </Show>
      </Show>
    </div>
  );
};

export default TodoList;
''',
}

# ── File Structure ───────────────────────────────────────────────────

_FILE_STRUCTURE: dict[str, str] = {
    "auth_store": "frontend/src/stores/auth.ts",
    "router": "frontend/src/routes/index.tsx",
    "layout": "frontend/src/components/Layout.tsx",
    "pages": "frontend/src/pages/",
    "app": "frontend/src/App.tsx",
    "components": "frontend/src/components/",
    "stores": "frontend/src/stores/",
}

# ── Generation Order ─────────────────────────────────────────────────

_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_store",
        "path": "frontend/src/stores/auth.ts",
        "description": (
            "Shared authentication store using createStore. "
            "Exposes reactive state, login/logout actions, and "
            "an initialize function that restores sessions from "
            "localStorage."
        ),
    },
    {
        "name": "router",
        "path": "frontend/src/routes/index.tsx",
        "description": (
            "Application router using @solidjs/router. "
            "Defines all top-level routes with lazy-loaded page "
            "components and a catch-all 404 route."
        ),
    },
    {
        "name": "layout",
        "path": "frontend/src/components/Layout.tsx",
        "description": (
            "Root layout component that wraps every route. "
            "Renders the navigation bar, Suspense boundary with "
            "a loading fallback, and ErrorBoundary."
        ),
    },
    {
        "name": "pages",
        "path": "frontend/src/pages/",
        "description": (
            "Individual page components (Home, Login, Dashboard, "
            "Settings, NotFound). Each page is a standalone component "
            "that uses stores and createResource for data."
        ),
    },
    {
        "name": "app",
        "path": "frontend/src/App.tsx",
        "description": (
            "Application entry point that initializes the auth "
            "store and renders the router. Wrapped in a top-level "
            "ErrorBoundary and Suspense boundary."
        ),
    },
)

# ── Register ─────────────────────────────────────────────────────────

SOLID_CONFIG = FrontendFrameworkConfig(
    name="solid",
    display_name="Solid.js",
    language="typescript",
    code_block_lang="typescript",
    component_extension=".tsx",
    file_structure=_FILE_STRUCTURE,
    rules=_RULES,
    golden_examples=_GOLDEN_EXAMPLES,
    generation_order=_GENERATION_ORDER,
)

register_frontend_framework(SOLID_CONFIG)
