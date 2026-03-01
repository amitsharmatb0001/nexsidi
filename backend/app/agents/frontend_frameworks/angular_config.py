"""Angular framework configuration for Aanya's frontend code generation.

Rules, golden examples, and file structure for generating production-grade
Angular 17+ standalone components with Signals, NgRx, and TypeScript strict mode.
"""

from __future__ import annotations

from app.agents.frontend_frameworks import FrontendFrameworkConfig, register_frontend_framework

ANGULAR_RULES: tuple[str, ...] = (
    "1. Use standalone components with `standalone: true` — NEVER use NgModules for component declarations. "
    "Every component, directive, and pipe must set `standalone: true` and import dependencies directly in the "
    "`imports` array of the `@Component` decorator",
    "2. Use Angular Signals for reactive state (`signal()`, `computed()`, `effect()`) — "
    "NEVER use `BehaviorSubject` or manual change detection for local component state. "
    "Use `input()` and `output()` signal-based APIs for component communication",
    "3. Use Angular Router with `provideRouter(routes)` and lazy-loaded routes via `loadComponent` — "
    "NEVER use `RouterModule.forRoot()`. Define routes in a `Routes` array and use "
    "`loadComponent: () => import('./pages/home.component').then(m => m.HomeComponent)`",
    "4. Use `@Injectable({ providedIn: 'root' })` for singleton services — "
    "NEVER provide services in component `providers` arrays unless scoping is intentional. "
    "Inject services via `inject()` function, not constructor injection",
    "5. Use RxJS observables with the `async` pipe for asynchronous streams — "
    "NEVER manually subscribe in components without unsubscribing. Prefer `toSignal()` from "
    "`@angular/core/rxjs-interop` to bridge RxJS into signals",
    "6. Use NgRx SignalStore (`signalStore()`) for global state management — "
    "NEVER use classic NgRx `Store`, `Actions`, `Reducers`, or `Effects`. Define stores with "
    "`withState()`, `withComputed()`, and `withMethods()` using the functional API",
    "7. Use TypeScript strict mode (`strict: true` in tsconfig) — "
    "NEVER use `any` type. Use proper interfaces, union types, and type guards. "
    "Enable `strictNullChecks`, `noImplicitAny`, and `strictPropertyInitialization`",
    "8. Use Angular Reactive Forms (`FormGroup`, `FormControl`, `Validators`) — "
    "NEVER use template-driven forms (`ngModel`). Import `ReactiveFormsModule` in the standalone "
    "component `imports` array and bind with `[formGroup]` and `formControlName`",
    "9. Use `HttpClient` from `@angular/common/http` with typed responses — "
    "NEVER use `fetch()` or `XMLHttpRequest`. Register with `provideHttpClient(withInterceptorsFromDi())` "
    "in `app.config.ts` and use generic type parameters: `http.get<User[]>('/api/users')`",
    "10. Follow Angular CLI file naming conventions — "
    "Components: `feature-name.component.ts`, Services: `feature-name.service.ts`, "
    "Guards: `auth.guard.ts`, Interceptors: `auth.interceptor.ts`. Use kebab-case for all file names",
    "11. Use `@defer` blocks for lazy loading heavy components in templates — "
    "Use `@if` and `@for` control flow blocks (Angular 17+ built-in control flow) — "
    "NEVER use `*ngIf`, `*ngFor`, or `*ngSwitch` structural directives",
    "12. NEVER use `// TODO`, `// FIXME`, `pass`, or empty function bodies — "
    "every function and method must have a REAL, COMPLETE implementation. "
    "NEVER invent import paths — use ONLY names from the contract and previously generated code",
    "13. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later. "
    "Use `@` path alias for project-internal imports (configured in tsconfig.json paths)",
)

ANGULAR_GOLDEN_EXAMPLES: dict[str, str] = {
    "component": '''\
import { Component, signal, computed, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ProductService } from '@app/services/product.service';
import { Product } from '@app/models/product.model';

@Component({
  selector: 'app-product-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <div class="product-list">
      <h2>Products ({{ totalCount() }})</h2>

      @if (loading()) {
        <div class="spinner">Loading...</div>
      }

      @for (product of filteredProducts(); track product.id) {
        <div class="product-card">
          <h3>
            <a [routerLink]="['/products', product.id]">{{ product.name }}</a>
          </h3>
          <p class="price">{{ product.price | currency }}</p>
          <p class="description">{{ product.description }}</p>
        </div>
      } @empty {
        <p class="no-results">No products found.</p>
      }

      <div class="actions">
        <input
          type="text"
          [value]="searchTerm()"
          (input)="onSearch($event)"
          placeholder="Search products..."
        />
      </div>
    </div>
  `,
})
export class ProductListComponent implements OnInit {
  private readonly productService = inject(ProductService);

  readonly products = signal<Product[]>([]);
  readonly searchTerm = signal('');
  readonly loading = signal(false);

  readonly filteredProducts = computed(() => {
    const term = this.searchTerm().toLowerCase();
    if (!term) {
      return this.products();
    }
    return this.products().filter(
      (p) =>
        p.name.toLowerCase().includes(term) ||
        p.description.toLowerCase().includes(term),
    );
  });

  readonly totalCount = computed(() => this.filteredProducts().length);

  ngOnInit(): void {
    this.loadProducts();
  }

  onSearch(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.searchTerm.set(target.value);
  }

  private loadProducts(): void {
    this.loading.set(true);
    this.productService.getAll().subscribe({
      next: (products) => {
        this.products.set(products);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
      },
    });
  }
}
''',
    "service": '''\
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, catchError, throwError } from 'rxjs';
import { User, CreateUserRequest, UserResponse, LoginRequest, AuthTokens } from '@app/models/user.model';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = '/api/auth';

  register(data: CreateUserRequest): Observable<UserResponse> {
    return this.http.post<UserResponse>(`${this.apiUrl}/register`, data).pipe(
      catchError((error) =>
        throwError(() => new Error(error.error?.detail ?? 'Registration failed')),
      ),
    );
  }

  login(credentials: LoginRequest): Observable<AuthTokens> {
    return this.http.post<AuthTokens>(`${this.apiUrl}/login`, credentials).pipe(
      catchError((error) =>
        throwError(() => new Error(error.error?.detail ?? 'Login failed')),
      ),
    );
  }

  getCurrentUser(): Observable<UserResponse> {
    return this.http.get<UserResponse>(`${this.apiUrl}/me`).pipe(
      catchError((error) =>
        throwError(() => new Error(error.error?.detail ?? 'Failed to fetch user')),
      ),
    );
  }

  logout(): void {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  }

  getAccessToken(): string | null {
    return localStorage.getItem('access_token');
  }

  setTokens(tokens: AuthTokens): void {
    localStorage.setItem('access_token', tokens.accessToken);
    localStorage.setItem('refresh_token', tokens.refreshToken);
  }

  isAuthenticated(): boolean {
    const token = this.getAccessToken();
    if (!token) {
      return false;
    }
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      return payload.exp * 1000 > Date.now();
    } catch {
      return false;
    }
  }
}
''',
    "store": '''\
import { computed, inject } from '@angular/core';
import {
  signalStore,
  withState,
  withComputed,
  withMethods,
  patchState,
} from '@ngrx/signals';
import { rxMethod } from '@ngrx/signals/rxjs-interop';
import { pipe, switchMap, tap } from 'rxjs';
import { tapResponse } from '@ngrx/operators';
import { ProductService } from '@app/services/product.service';
import { Product } from '@app/models/product.model';

interface ProductState {
  products: Product[];
  selectedProduct: Product | null;
  loading: boolean;
  error: string | null;
}

const initialState: ProductState = {
  products: [],
  selectedProduct: null,
  loading: false,
  error: null,
};

export const ProductStore = signalStore(
  { providedIn: 'root' },
  withState(initialState),
  withComputed((store) => ({
    productCount: computed(() => store.products().length),
    hasError: computed(() => store.error() !== null),
    sortedProducts: computed(() =>
      [...store.products()].sort((a, b) => a.name.localeCompare(b.name)),
    ),
  })),
  withMethods((store, productService = inject(ProductService)) => ({
    loadProducts: rxMethod<void>(
      pipe(
        tap(() => patchState(store, { loading: true, error: null })),
        switchMap(() =>
          productService.getAll().pipe(
            tapResponse({
              next: (products) =>
                patchState(store, { products, loading: false }),
              error: (error: Error) =>
                patchState(store, {
                  loading: false,
                  error: error.message,
                }),
            }),
          ),
        ),
      ),
    ),
    selectProduct(product: Product): void {
      patchState(store, { selectedProduct: product });
    },
    clearSelection(): void {
      patchState(store, { selectedProduct: null });
    },
    clearError(): void {
      patchState(store, { error: null });
    },
  })),
);
''',
    "routing": '''\
import { Routes } from '@angular/router';
import { authGuard } from '@app/guards/auth.guard';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/home/home.component').then((m) => m.HomeComponent),
    title: 'Home',
  },
  {
    path: 'login',
    loadComponent: () =>
      import('./pages/login/login.component').then((m) => m.LoginComponent),
    title: 'Login',
  },
  {
    path: 'register',
    loadComponent: () =>
      import('./pages/register/register.component').then(
        (m) => m.RegisterComponent,
      ),
    title: 'Register',
  },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./pages/dashboard/dashboard.component').then(
            (m) => m.DashboardComponent,
          ),
        title: 'Dashboard',
      },
      {
        path: 'products',
        loadComponent: () =>
          import('./pages/products/product-list.component').then(
            (m) => m.ProductListComponent,
          ),
        title: 'Products',
      },
      {
        path: 'products/:id',
        loadComponent: () =>
          import('./pages/products/product-detail.component').then(
            (m) => m.ProductDetailComponent,
          ),
        title: 'Product Detail',
      },
    ],
  },
  {
    path: '**',
    loadComponent: () =>
      import('./pages/not-found/not-found.component').then(
        (m) => m.NotFoundComponent,
      ),
    title: 'Page Not Found',
  },
];
''',
}

ANGULAR_FILE_STRUCTURE: dict[str, str] = {
    "components": "frontend/src/app/components/",
    "services": "frontend/src/app/services/",
    "store": "frontend/src/app/store/",
    "pages": "frontend/src/app/pages/",
    "guards": "frontend/src/app/guards/",
    "interceptors": "frontend/src/app/interceptors/",
    "models": "frontend/src/app/models/",
    "routing": "frontend/src/app/app.routes.ts",
    "app_config": "frontend/src/app/app.config.ts",
    "app_component": "frontend/src/app/app.component.ts",
    "styles": "frontend/src/styles.scss",
    "main": "frontend/src/main.ts",
}

ANGULAR_GENERATION_ORDER: tuple[dict[str, str], ...] = (
    {
        "name": "auth_service",
        "path": "frontend/src/app/services/auth.service.ts",
        "description": "Authentication service with login, register, token management, and HTTP calls to the backend auth API",
    },
    {
        "name": "routing",
        "path": "frontend/src/app/app.routes.ts",
        "description": "Root Routes configuration with lazy-loaded pages, auth guards on protected routes, and wildcard fallback",
    },
    {
        "name": "layout",
        "path": "frontend/src/app/components/layout/layout.component.ts",
        "description": "Shell layout component with responsive navigation bar, sidebar, router-outlet, and conditional auth links",
    },
    {
        "name": "pages",
        "path": "frontend/src/app/pages/",
        "description": "Page components for each route: home, login, register, dashboard, and entity CRUD pages with forms and tables",
    },
    {
        "name": "app_component",
        "path": "frontend/src/app/app.component.ts",
        "description": "Root AppComponent that bootstraps the layout, provides the router-outlet, and initializes global services",
    },
)

ANGULAR_CONFIG = FrontendFrameworkConfig(
    name="angular",
    display_name="Angular",
    language="typescript",
    code_block_lang="typescript",
    component_extension=".ts",
    file_structure=ANGULAR_FILE_STRUCTURE,
    rules=ANGULAR_RULES,
    golden_examples=ANGULAR_GOLDEN_EXAMPLES,
    generation_order=ANGULAR_GENERATION_ORDER,
)

register_frontend_framework(ANGULAR_CONFIG)
