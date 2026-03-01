"""Vue.js / Nuxt frontend framework configuration for Aanya's code generation.

Rules, golden examples, and file structure for generating production-grade
Vue 3 Composition API + Pinia + Vue Router + TypeScript frontends.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

VUE_RULES: tuple[str, ...] = (
    "1. Use Vue 3 Composition API with `<script setup lang=\"ts\">` — NEVER use Options API. "
    "All components must use the `<script setup>` sugar syntax for cleaner, more concise code",
    "2. Use Pinia for state management — NEVER use Vuex. "
    "Define stores with `defineStore()` using the setup function syntax: "
    "`export const useAuthStore = defineStore('auth', () => { ... })`",
    "3. Use Vue Router with typed route definitions — NEVER use string-based navigation without types. "
    "Use `useRouter()` and `useRoute()` composables inside `<script setup>` blocks",
    "4. Use `defineComponent` only when `<script setup>` is insufficient (e.g. custom render functions) — "
    "prefer `<script setup lang=\"ts\">` for all standard Single File Components",
    "5. Use `ref()` for primitive reactive state and `reactive()` for objects — "
    "NEVER mutate props directly. Use `toRefs()` when destructuring reactive objects to preserve reactivity",
    "6. Use `computed()` for derived state and `watch()`/`watchEffect()` for side effects — "
    "NEVER compute values inside the template. Extract complex logic into composables",
    "7. Use strict TypeScript throughout — NEVER use `any` type. "
    "Define `interface` or `type` for all props via `defineProps<Props>()`, emits via `defineEmits<Emits>()`, "
    "and store state. Enable `strict: true` in tsconfig.json",
    "8. Use Tailwind CSS for styling — NEVER use scoped CSS or CSS modules for layout. "
    "Use utility classes in templates. Tailwind config lives at `tailwind.config.ts`",
    "9. Follow accessibility best practices — use semantic HTML elements (`<nav>`, `<main>`, `<section>`), "
    "add `aria-label` to interactive elements, ensure keyboard navigation with `tabindex`, "
    "and use `role` attributes where native semantics are insufficient",
    "10. Use composables (use* pattern) for reusable logic — extract shared stateful logic into "
    "`frontend/src/composables/use*.ts` files. Composables must return refs and functions, "
    "NEVER return raw reactive objects without wrapping",
    "11. Use `defineProps` with TypeScript generics and `withDefaults` for default values — "
    "NEVER use the runtime props declaration. Use `defineExpose` only when parent components "
    "need imperative access to child methods",
    "12. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use `@/` path alias for project-internal imports (configured in vite.config.ts). "
    "Output ONLY the code file — no markdown fences, no explanations, no TODO comments",
)

VUE_GOLDEN_EXAMPLES: dict[str, str] = {
    "component": '''\
<script setup lang="ts">
import { ref, computed } from "vue";
import { useAuthStore } from "@/stores/auth";

interface Props {
  title: string;
  subtitle?: string;
  variant?: "primary" | "secondary";
}

const props = withDefaults(defineProps<Props>(), {
  subtitle: "",
  variant: "primary",
});

const emit = defineEmits<{
  (e: "close"): void;
  (e: "submit", value: string): void;
}>();

const authStore = useAuthStore();
const inputValue = ref("");
const isValid = computed(() => inputValue.value.trim().length > 0);

const handleSubmit = (): void => {
  if (!isValid.value) return;
  emit("submit", inputValue.value.trim());
  inputValue.value = "";
};
</script>

<template>
  <div
    :class="[
      'rounded-lg border p-6 shadow-sm',
      variant === 'primary' ? 'border-blue-200 bg-white' : 'border-gray-200 bg-gray-50',
    ]"
  >
    <header class="mb-4 flex items-center justify-between">
      <div>
        <h2 class="text-lg font-semibold text-gray-900">{{ title }}</h2>
        <p v-if="subtitle" class="mt-1 text-sm text-gray-500">{{ subtitle }}</p>
      </div>
      <button
        type="button"
        aria-label="Close dialog"
        class="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
        @click="emit('close')"
      >
        <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </header>

    <form @submit.prevent="handleSubmit" class="space-y-4">
      <div>
        <label for="input-field" class="block text-sm font-medium text-gray-700">
          Value
        </label>
        <input
          id="input-field"
          v-model="inputValue"
          type="text"
          class="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          placeholder="Enter a value..."
        />
      </div>
      <button
        type="submit"
        :disabled="!isValid"
        class="inline-flex w-full justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Submit
      </button>
    </form>

    <p v-if="authStore.isAuthenticated" class="mt-3 text-xs text-gray-400">
      Signed in as {{ authStore.user?.email }}
    </p>
  </div>
</template>
''',
    "store": '''\
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { useRouter } from "vue-router";

export interface User {
  id: number;
  email: string;
  fullName: string;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  fullName: string;
  password: string;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
}

export const useAuthStore = defineStore("auth", () => {
  const router = useRouter();

  const user = ref<User | null>(null);
  const accessToken = ref<string | null>(localStorage.getItem("access_token"));
  const refreshToken = ref<string | null>(localStorage.getItem("refresh_token"));
  const isLoading = ref(false);
  const error = ref<string | null>(null);

  const isAuthenticated = computed(() => !!accessToken.value && !!user.value);
  const userDisplayName = computed(() => user.value?.fullName ?? "Guest");

  const setTokens = (tokens: AuthTokens): void => {
    accessToken.value = tokens.accessToken;
    refreshToken.value = tokens.refreshToken;
    localStorage.setItem("access_token", tokens.accessToken);
    localStorage.setItem("refresh_token", tokens.refreshToken);
  };

  const clearSession = (): void => {
    user.value = null;
    accessToken.value = null;
    refreshToken.value = null;
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
  };

  const login = async (credentials: LoginCredentials): Promise<void> => {
    isLoading.value = true;
    error.value = null;
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error ?? "Login failed");
      }
      const data: { user: User; tokens: AuthTokens } = await response.json();
      user.value = data.user;
      setTokens(data.tokens);
      await router.push({ name: "dashboard" });
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Login failed";
      throw err;
    } finally {
      isLoading.value = false;
    }
  };

  const register = async (payload: RegisterPayload): Promise<void> => {
    isLoading.value = true;
    error.value = null;
    try {
      const response = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error ?? "Registration failed");
      }
      const data: { user: User; tokens: AuthTokens } = await response.json();
      user.value = data.user;
      setTokens(data.tokens);
      await router.push({ name: "dashboard" });
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Registration failed";
      throw err;
    } finally {
      isLoading.value = false;
    }
  };

  const logout = async (): Promise<void> => {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken.value}` },
      });
    } finally {
      clearSession();
      await router.push({ name: "login" });
    }
  };

  const fetchCurrentUser = async (): Promise<void> => {
    if (!accessToken.value) return;
    isLoading.value = true;
    try {
      const response = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${accessToken.value}` },
      });
      if (!response.ok) {
        clearSession();
        return;
      }
      const data: { user: User } = await response.json();
      user.value = data.user;
    } catch {
      clearSession();
    } finally {
      isLoading.value = false;
    }
  };

  return {
    user,
    accessToken,
    isLoading,
    error,
    isAuthenticated,
    userDisplayName,
    login,
    register,
    logout,
    fetchCurrentUser,
  };
});
''',
    "page": '''\
<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

interface DashboardStats {
  totalUsers: number;
  totalProducts: number;
  recentActivity: Array<{
    id: number;
    action: string;
    timestamp: string;
  }>;
}

const router = useRouter();
const authStore = useAuthStore();

const stats = ref<DashboardStats | null>(null);
const isLoading = ref(true);
const errorMessage = ref<string | null>(null);

const fetchDashboardStats = async (): Promise<void> => {
  isLoading.value = true;
  errorMessage.value = null;
  try {
    const response = await fetch("/api/dashboard/stats", {
      headers: { Authorization: `Bearer ${authStore.accessToken}` },
    });
    if (!response.ok) {
      if (response.status === 401) {
        await authStore.logout();
        return;
      }
      throw new Error("Failed to load dashboard data");
    }
    stats.value = await response.json();
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : "An error occurred";
  } finally {
    isLoading.value = false;
  }
};

onMounted(async () => {
  if (!authStore.isAuthenticated) {
    await router.push({ name: "login" });
    return;
  }
  await fetchDashboardStats();
});
</script>

<template>
  <main class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
    <header class="mb-8">
      <h1 class="text-2xl font-bold text-gray-900">Dashboard</h1>
      <p class="mt-1 text-sm text-gray-500">
        Welcome back, {{ authStore.userDisplayName }}
      </p>
    </header>

    <div v-if="isLoading" class="flex items-center justify-center py-12" role="status">
      <svg
        class="h-8 w-8 animate-spin text-blue-600"
        fill="none"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span class="sr-only">Loading dashboard...</span>
    </div>

    <div
      v-else-if="errorMessage"
      role="alert"
      class="rounded-md border border-red-200 bg-red-50 p-4"
    >
      <p class="text-sm font-medium text-red-800">{{ errorMessage }}</p>
      <button
        type="button"
        class="mt-2 text-sm font-semibold text-red-600 hover:text-red-500"
        @click="fetchDashboardStats"
      >
        Try again
      </button>
    </div>

    <template v-else-if="stats">
      <div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        <section
          class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
          aria-label="Total users"
        >
          <h3 class="text-sm font-medium text-gray-500">Total Users</h3>
          <p class="mt-2 text-3xl font-bold text-gray-900">{{ stats.totalUsers }}</p>
        </section>

        <section
          class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm"
          aria-label="Total products"
        >
          <h3 class="text-sm font-medium text-gray-500">Total Products</h3>
          <p class="mt-2 text-3xl font-bold text-gray-900">{{ stats.totalProducts }}</p>
        </section>
      </div>

      <section class="mt-8" aria-label="Recent activity">
        <h2 class="mb-4 text-lg font-semibold text-gray-900">Recent Activity</h2>
        <ul v-if="stats.recentActivity.length > 0" class="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
          <li
            v-for="activity in stats.recentActivity"
            :key="activity.id"
            class="flex items-center justify-between px-6 py-4"
          >
            <span class="text-sm text-gray-700">{{ activity.action }}</span>
            <time class="text-xs text-gray-400" :datetime="activity.timestamp">
              {{ new Date(activity.timestamp).toLocaleDateString() }}
            </time>
          </li>
        </ul>
        <p v-else class="text-sm text-gray-500">No recent activity.</p>
      </section>
    </template>
  </main>
</template>
''',
    "composable": '''\
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

interface UseAuthReturn {
  isAuthenticated: ReturnType<typeof computed<boolean>>;
  isLoading: ReturnType<typeof ref<boolean>>;
  user: ReturnType<typeof computed<import("@/stores/auth").User | null>>;
  error: ReturnType<typeof ref<string | null>>;
  login: (email: string, password: string) => Promise<boolean>;
  register: (email: string, fullName: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  requireAuth: () => Promise<boolean>;
}

export const useAuth = (): UseAuthReturn => {
  const router = useRouter();
  const authStore = useAuthStore();

  const isLoading = ref(false);
  const error = ref<string | null>(null);

  const isAuthenticated = computed(() => authStore.isAuthenticated);
  const user = computed(() => authStore.user);

  const login = async (email: string, password: string): Promise<boolean> => {
    isLoading.value = true;
    error.value = null;
    try {
      await authStore.login({ email, password });
      return true;
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Login failed";
      return false;
    } finally {
      isLoading.value = false;
    }
  };

  const register = async (
    email: string,
    fullName: string,
    password: string,
  ): Promise<boolean> => {
    isLoading.value = true;
    error.value = null;
    try {
      await authStore.register({ email, fullName, password });
      return true;
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Registration failed";
      return false;
    } finally {
      isLoading.value = false;
    }
  };

  const logout = async (): Promise<void> => {
    await authStore.logout();
  };

  const requireAuth = async (): Promise<boolean> => {
    if (isAuthenticated.value) return true;
    await authStore.fetchCurrentUser();
    if (!isAuthenticated.value) {
      await router.push({ name: "login" });
      return false;
    }
    return true;
  };

  onMounted(async () => {
    if (authStore.accessToken && !authStore.user) {
      await authStore.fetchCurrentUser();
    }
  });

  return {
    isAuthenticated,
    isLoading,
    user,
    error,
    login,
    register,
    logout,
    requireAuth,
  };
};
''',
}

VUE_FILE_STRUCTURE: dict[str, str] = {
    "components": "frontend/src/components/",
    "stores": "frontend/src/stores/",
    "pages": "frontend/src/pages/",
    "composables": "frontend/src/composables/",
    "layouts": "frontend/src/layouts/",
    "router": "frontend/src/router/index.ts",
    "types": "frontend/src/types/index.ts",
    "app_entry": "frontend/src/App.vue",
    "main": "frontend/src/main.ts",
    "vite_config": "frontend/vite.config.ts",
    "tailwind_config": "frontend/tailwind.config.ts",
}

VUE_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_store",
        "path": "frontend/src/stores/auth.ts",
        "description": "Pinia auth store with login, register, logout, and token management using the setup store syntax",
    },
    {
        "name": "layout",
        "path": "frontend/src/layouts/DefaultLayout.vue",
        "description": "Default layout component with responsive navigation bar, sidebar, and main content slot using Tailwind CSS",
    },
    {
        "name": "pages",
        "path": "frontend/src/pages/",
        "description": "Page components for each route (login, register, dashboard, etc.) using Composition API with script setup",
    },
    {
        "name": "app",
        "path": "frontend/src/App.vue",
        "description": "Root App component with RouterView, Pinia provider, and global error boundary",
    },
)

VUE_CONFIG = FrontendFrameworkConfig(
    name="vue",
    display_name="Vue.js",
    language="typescript",
    code_block_lang="vue",
    component_extension=".vue",
    file_structure=VUE_FILE_STRUCTURE,
    rules=VUE_RULES,
    golden_examples=VUE_GOLDEN_EXAMPLES,
    generation_order=VUE_GENERATION_ORDER,
)

register_frontend_framework(VUE_CONFIG)
