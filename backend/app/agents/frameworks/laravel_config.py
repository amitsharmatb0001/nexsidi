"""Laravel framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Laravel + Eloquent + FormRequest + API Resources + PostgreSQL backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

LARAVEL_RULES: tuple[str, ...] = (
    "1. Use Eloquent ORM models — NEVER use raw DB queries or Query Builder for standard CRUD. "
    "Extend `Illuminate\\Database\\Eloquent\\Model`. Define `$fillable`, `$casts`, and relationships",
    "2. Use FormRequest classes for validation — NEVER validate inline in controllers. "
    "Create a dedicated `App\\Http\\Requests\\*Request` with `rules()` and `authorize()` methods",
    "3. Use API Resource classes for response transformation — NEVER return raw models or arrays. "
    "Extend `Illuminate\\Http\\Resources\\Json\\JsonResource` with a `toArray()` method",
    "4. Use resourceful controllers — extend `Controller` and implement "
    "`index`, `store`, `show`, `update`, `destroy`. Use `__invoke` for single-action controllers",
    "5. Use route model binding for single-resource lookups — "
    "type-hint the model in controller method signatures. NEVER call `Model::find()` then check for null",
    "6. Use Eloquent scopes for reusable query constraints — "
    "define `scopeActive`, `scopeByOwner`, etc. NEVER duplicate where clauses across methods",
    "7. Use Laravel migrations with `Schema::create()` and `Schema::table()` — "
    "NEVER modify the database manually. Every column, index, and foreign key goes through a migration",
    "8. Use middleware for authentication and authorization — "
    "apply `auth:sanctum` middleware to protected routes. Use Gate/Policy classes for model-level authorization",
    "9. Use Laravel Sanctum for API authentication — NEVER use Passport for simple token auth. "
    "Issue tokens with `$user->createToken('api')` and protect routes with `auth:sanctum`",
    "10. Every Eloquent model MUST define `$fillable` for mass assignment protection, "
    "`$casts` for attribute casting, and relationship methods returning "
    "`HasMany`, `BelongsTo`, `HasOne`, etc.",
    "11. NEVER use 'pass', '// TODO', '...', or empty method bodies — "
    "every function must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY fully qualified class names from the contract "
    "and previously generated code. Use `use` statements at the top of each file",
    "13. Use `DB::transaction()` for multi-step write operations — "
    "import from `Illuminate\\Support\\Facades\\DB`. Return the result from inside the closure",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

LARAVEL_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
<?php

namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Factories\\HasFactory;
use Illuminate\\Database\\Eloquent\\Relations\\HasMany;
use Illuminate\\Foundation\\Auth\\User as Authenticatable;
use Illuminate\\Notifications\\Notifiable;
use Laravel\\Sanctum\\HasApiTokens;

class User extends Authenticatable
{
    use HasApiTokens, HasFactory, Notifiable;

    protected $table = 'users';

    protected $fillable = [
        'full_name',
        'email',
        'password',
    ];

    protected $hidden = [
        'password',
        'remember_token',
    ];

    protected $casts = [
        'email_verified_at' => 'datetime',
        'is_active' => 'boolean',
        'created_at' => 'datetime',
        'updated_at' => 'datetime',
    ];

    public function products(): HasMany
    {
        return $this->hasMany(Product::class, 'owner_id');
    }

    public function scopeActive($query)
    {
        return $query->where('is_active', true);
    }
}


class Product extends Model
{
    use HasFactory;

    protected $table = 'products';

    protected $fillable = [
        'name',
        'description',
        'price',
        'owner_id',
    ];

    protected $casts = [
        'price' => 'decimal:2',
        'created_at' => 'datetime',
        'updated_at' => 'datetime',
    ];

    public function owner(): BelongsTo
    {
        return $this->belongsTo(User::class, 'owner_id');
    }

    public function scopeByOwner($query, int $ownerId)
    {
        return $query->where('owner_id', $ownerId);
    }
}
''',
    "controllers": '''\
<?php

namespace App\\Http\\Controllers;

use App\\Http\\Requests\\StoreProductRequest;
use App\\Http\\Requests\\UpdateProductRequest;
use App\\Http\\Resources\\ProductResource;
use App\\Models\\Product;
use Illuminate\\Http\\JsonResponse;
use Illuminate\\Http\\Request;
use Illuminate\\Http\\Resources\\Json\\AnonymousResourceCollection;

class ProductController extends Controller
{
    public function __construct()
    {
        $this->middleware('auth:sanctum');
    }

    public function index(Request $request): AnonymousResourceCollection
    {
        $products = Product::query()
            ->byOwner($request->user()->id)
            ->with('owner')
            ->orderByDesc('created_at')
            ->paginate(15);

        return ProductResource::collection($products);
    }

    public function store(StoreProductRequest $request): JsonResponse
    {
        $product = Product::create([
            ...$request->validated(),
            'owner_id' => $request->user()->id,
        ]);

        return (new ProductResource($product->load('owner')))
            ->response()
            ->setStatusCode(201);
    }

    public function show(Product $product): ProductResource
    {
        $this->authorize('view', $product);

        return new ProductResource($product->load('owner'));
    }

    public function update(UpdateProductRequest $request, Product $product): ProductResource
    {
        $this->authorize('update', $product);

        $product->update($request->validated());

        return new ProductResource($product->fresh('owner'));
    }

    public function destroy(Product $product): JsonResponse
    {
        $this->authorize('delete', $product);

        $product->delete();

        return response()->json(null, 204);
    }
}
''',
    "migrations": '''\
<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('full_name', 255);
            $table->string('email', 255)->unique();
            $table->timestamp('email_verified_at')->nullable();
            $table->string('password');
            $table->boolean('is_active')->default(true);
            $table->rememberToken();
            $table->timestamps();

            $table->index('email');
            $table->index('is_active');
        });

        Schema::create('products', function (Blueprint $table) {
            $table->id();
            $table->string('name', 255);
            $table->text('description')->nullable();
            $table->decimal('price', 10, 2);
            $table->foreignId('owner_id')
                ->constrained('users')
                ->cascadeOnDelete();
            $table->timestamps();

            $table->index('owner_id');
            $table->index('created_at');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('products');
        Schema::dropIfExists('users');
    }
};
''',
    "form_requests": '''\
<?php

namespace App\\Http\\Requests;

use Illuminate\\Foundation\\Http\\FormRequest;
use Illuminate\\Validation\\Rule;

class StoreProductRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user() !== null;
    }

    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:255'],
            'description' => ['nullable', 'string', 'max:5000'],
            'price' => ['required', 'numeric', 'min:0.01', 'max:999999.99'],
        ];
    }

    public function messages(): array
    {
        return [
            'name.required' => 'Product name is required.',
            'name.max' => 'Product name must not exceed 255 characters.',
            'price.required' => 'Product price is required.',
            'price.min' => 'Product price must be at least 0.01.',
            'price.max' => 'Product price must not exceed 999,999.99.',
        ];
    }
}


class UpdateProductRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user() !== null
            && $this->route('product')->owner_id === $this->user()->id;
    }

    public function rules(): array
    {
        return [
            'name' => ['sometimes', 'required', 'string', 'max:255'],
            'description' => ['nullable', 'string', 'max:5000'],
            'price' => ['sometimes', 'required', 'numeric', 'min:0.01', 'max:999999.99'],
        ];
    }
}


class StoreUserRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'full_name' => ['required', 'string', 'max:255'],
            'email' => ['required', 'email', 'max:255', Rule::unique('users', 'email')],
            'password' => ['required', 'string', 'min:8', 'max:128', 'confirmed'],
        ];
    }

    public function messages(): array
    {
        return [
            'email.unique' => 'This email address is already registered.',
            'password.confirmed' => 'Password confirmation does not match.',
            'password.min' => 'Password must be at least 8 characters.',
        ];
    }
}
''',
}

LARAVEL_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/app/Models/",
    "controllers": "backend/app/Http/Controllers/",
    "migrations": "backend/database/migrations/",
    "form_requests": "backend/app/Http/Requests/",
    "resources": "backend/app/Http/Resources/",
    "policies": "backend/app/Policies/",
    "middleware": "backend/app/Http/Middleware/",
    "routes": "backend/routes/api.php",
    "services": "backend/app/Services/",
    "tests": "backend/tests/Feature/",
    "seeders": "backend/database/seeders/",
    "config": "backend/config/",
}


LARAVEL_CONFIG = FrameworkConfig(
    name="laravel",
    display_name="Laravel",
    language="php",
    code_block_lang="php",
    error_comment_prefix="//",
    file_structure=LARAVEL_FILE_STRUCTURE,
    rules=LARAVEL_RULES,
    golden_examples=LARAVEL_GOLDEN_EXAMPLES,
)

register_framework(LARAVEL_CONFIG)
