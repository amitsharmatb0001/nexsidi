"""ASP.NET Core framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
ASP.NET Core + Entity Framework Core + AutoMapper + FluentValidation backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

ASPNET_RULES: tuple[str, ...] = (
    "1. Use Entity Framework Core with Code-First approach — define entities as C# classes with "
    "navigation properties and configure via `OnModelCreating()` or `IEntityTypeConfiguration<T>`. "
    "NEVER use Database-First or raw ADO.NET",
    "2. Use DTOs with AutoMapper for request/response shaping — define `Profile` classes with "
    "`CreateMap<TSource, TDest>()`. NEVER expose EF Core entities directly in API responses",
    "3. Use `[ApiController]` attribute on all controllers — inherit from `ControllerBase`, "
    "use `[Route(\"api/[controller]\")]` and `[HttpGet]`/`[HttpPost]`/`[HttpPut]`/`[HttpDelete]` "
    "attributes on action methods",
    "4. Use FluentValidation for request validation — create a validator class inheriting "
    "`AbstractValidator<T>` with rules in the constructor. Register validators with "
    "`AddFluentValidation()` in DI. NEVER use DataAnnotations for API validation",
    "5. Use constructor-based dependency injection for all services — register services in "
    "`Program.cs` with `builder.Services.AddScoped<IService, Service>()`. "
    "NEVER use `new` to instantiate services inside controllers or other services",
    "6. Use `IActionResult` or `ActionResult<T>` return types on all controller actions — "
    "return `Ok()`, `CreatedAtAction()`, `NotFound()`, `BadRequest()`. "
    "NEVER return raw objects without wrapping in an action result",
    "7. Use EF Core migrations for database schema management — create with "
    "`dotnet ef migrations add <Name>` and apply with `dotnet ef database update`. "
    "NEVER modify the database schema manually or use `EnsureCreated()` in production",
    "8. Use async/await for ALL database and I/O operations — controller actions and service "
    "methods MUST be `async Task<T>`. Use `ToListAsync()`, `FirstOrDefaultAsync()`, "
    "`SaveChangesAsync()`. NEVER use synchronous EF Core methods",
    "9. Use middleware pipeline ordering: `UseAuthentication()` before `UseAuthorization()`, "
    "exception handling middleware first. Custom middleware implements `IMiddleware` or uses "
    "the `RequestDelegate` convention",
    "10. Every entity MUST have a primary key property `Id` of type `int` or `Guid`, "
    "and `CreatedAt`/`UpdatedAt` DateTime properties. Configure `UpdatedAt` with a "
    "`ValueGeneratedOnAddOrUpdate()` trigger or override `SaveChangesAsync()`",
    "11. NEVER use '// TODO', 'throw new NotImplementedException()', or empty method bodies — "
    "every method must have a REAL, COMPLETE implementation",
    "12. NEVER invent namespace or using paths — use ONLY names from the contract and previously "
    "generated code. Follow the project namespace convention `ProjectName.Folder.ClassName`",
    "13. Use `ConfigureAwait(false)` in library/service code — use `CancellationToken` parameters "
    "on async methods that accept them. Pass `cancellationToken` to all EF Core async calls",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about "
    "what to add later",
)

ASPNET_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;
using Microsoft.EntityFrameworkCore;

namespace Backend.Models;

public class User
{
    [Key]
    [DatabaseGenerated(DatabaseGeneratedOption.Identity)]
    public int Id { get; set; }

    [Required]
    [MaxLength(255)]
    public string Email { get; set; } = string.Empty;

    [Required]
    [MaxLength(255)]
    public string HashedPassword { get; set; } = string.Empty;

    [Required]
    [MaxLength(255)]
    public string FullName { get; set; } = string.Empty;

    public bool IsActive { get; set; } = true;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    public ICollection<Product> Products { get; set; } = new List<Product>();
}

public class Product
{
    [Key]
    [DatabaseGenerated(DatabaseGeneratedOption.Identity)]
    public int Id { get; set; }

    [Required]
    [MaxLength(255)]
    public string Name { get; set; } = string.Empty;

    public string Description { get; set; } = string.Empty;

    [Column(TypeName = "decimal(18,2)")]
    public decimal Price { get; set; }

    public int OwnerId { get; set; }

    [ForeignKey(nameof(OwnerId))]
    public User Owner { get; set; } = null!;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
}

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<User> Users => Set<User>();
    public DbSet<Product> Products => Set<Product>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<User>(entity =>
        {
            entity.HasIndex(u => u.Email).IsUnique();
            entity.Property(u => u.CreatedAt).HasDefaultValueSql("NOW()");
            entity.Property(u => u.UpdatedAt).HasDefaultValueSql("NOW()");
        });

        modelBuilder.Entity<Product>(entity =>
        {
            entity.HasOne(p => p.Owner)
                  .WithMany(u => u.Products)
                  .HasForeignKey(p => p.OwnerId)
                  .OnDelete(DeleteBehavior.Cascade);
            entity.Property(p => p.CreatedAt).HasDefaultValueSql("NOW()");
            entity.Property(p => p.UpdatedAt).HasDefaultValueSql("NOW()");
        });
    }

    public override async Task<int> SaveChangesAsync(CancellationToken cancellationToken = default)
    {
        foreach (var entry in ChangeTracker.Entries()
            .Where(e => e.State == EntityState.Modified))
        {
            if (entry.Metadata.FindProperty("UpdatedAt") is not null)
            {
                entry.Property("UpdatedAt").CurrentValue = DateTime.UtcNow;
            }
        }
        return await base.SaveChangesAsync(cancellationToken).ConfigureAwait(false);
    }
}
''',
    "controllers": '''\
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using AutoMapper;
using Backend.DTOs;
using Backend.Models;
using Backend.Services;

namespace Backend.Controllers;

[ApiController]
[Route("api/[controller]")]
public class UsersController : ControllerBase
{
    private readonly IUserService _userService;
    private readonly IMapper _mapper;

    public UsersController(IUserService userService, IMapper mapper)
    {
        _userService = userService;
        _mapper = mapper;
    }

    [HttpGet]
    [Authorize]
    public async Task<ActionResult<IEnumerable<UserResponseDto>>> GetAll(
        CancellationToken cancellationToken)
    {
        var users = await _userService.GetAllAsync(cancellationToken).ConfigureAwait(false);
        var dtos = _mapper.Map<IEnumerable<UserResponseDto>>(users);
        return Ok(dtos);
    }

    [HttpGet("{id:int}")]
    [Authorize]
    public async Task<ActionResult<UserResponseDto>> GetById(
        int id, CancellationToken cancellationToken)
    {
        var user = await _userService.GetByIdAsync(id, cancellationToken).ConfigureAwait(false);
        if (user is null)
        {
            return NotFound(new { error = "User not found" });
        }
        var dto = _mapper.Map<UserResponseDto>(user);
        return Ok(dto);
    }

    [HttpPost]
    [AllowAnonymous]
    public async Task<ActionResult<UserResponseDto>> Create(
        [FromBody] CreateUserDto createDto, CancellationToken cancellationToken)
    {
        var existing = await _userService.GetByEmailAsync(createDto.Email, cancellationToken)
            .ConfigureAwait(false);
        if (existing is not null)
        {
            return Conflict(new { error = "Email already registered" });
        }
        var user = await _userService.CreateAsync(createDto, cancellationToken)
            .ConfigureAwait(false);
        var dto = _mapper.Map<UserResponseDto>(user);
        return CreatedAtAction(nameof(GetById), new { id = user.Id }, dto);
    }
}
''',
    "dto": '''\
using AutoMapper;
using Backend.Models;
using FluentValidation;

namespace Backend.DTOs;

public class CreateUserDto
{
    public string Email { get; set; } = string.Empty;
    public string FullName { get; set; } = string.Empty;
    public string Password { get; set; } = string.Empty;
}

public class UserResponseDto
{
    public int Id { get; set; }
    public string Email { get; set; } = string.Empty;
    public string FullName { get; set; } = string.Empty;
    public bool IsActive { get; set; }
    public DateTime CreatedAt { get; set; }
}

public class CreateUserDtoValidator : AbstractValidator<CreateUserDto>
{
    public CreateUserDtoValidator()
    {
        RuleFor(x => x.Email)
            .NotEmpty().WithMessage("Email is required")
            .EmailAddress().WithMessage("Invalid email format")
            .MaximumLength(255);

        RuleFor(x => x.FullName)
            .NotEmpty().WithMessage("Full name is required")
            .MaximumLength(255);

        RuleFor(x => x.Password)
            .NotEmpty().WithMessage("Password is required")
            .MinimumLength(8).WithMessage("Password must be at least 8 characters")
            .MaximumLength(128);
    }
}

public class UserMappingProfile : Profile
{
    public UserMappingProfile()
    {
        CreateMap<User, UserResponseDto>();
        CreateMap<CreateUserDto, User>()
            .ForMember(dest => dest.HashedPassword, opt => opt.Ignore())
            .ForMember(dest => dest.Id, opt => opt.Ignore())
            .ForMember(dest => dest.CreatedAt, opt => opt.Ignore())
            .ForMember(dest => dest.UpdatedAt, opt => opt.Ignore())
            .ForMember(dest => dest.IsActive, opt => opt.Ignore())
            .ForMember(dest => dest.Products, opt => opt.Ignore());
    }
}
''',
    "services": '''\
using Microsoft.EntityFrameworkCore;
using Backend.DTOs;
using Backend.Models;

namespace Backend.Services;

public interface IUserService
{
    Task<IEnumerable<User>> GetAllAsync(CancellationToken cancellationToken = default);
    Task<User?> GetByIdAsync(int id, CancellationToken cancellationToken = default);
    Task<User?> GetByEmailAsync(string email, CancellationToken cancellationToken = default);
    Task<User> CreateAsync(CreateUserDto createDto, CancellationToken cancellationToken = default);
}

public class UserService : IUserService
{
    private readonly AppDbContext _context;

    public UserService(AppDbContext context)
    {
        _context = context;
    }

    public async Task<IEnumerable<User>> GetAllAsync(CancellationToken cancellationToken = default)
    {
        return await _context.Users
            .AsNoTracking()
            .OrderByDescending(u => u.CreatedAt)
            .ToListAsync(cancellationToken)
            .ConfigureAwait(false);
    }

    public async Task<User?> GetByIdAsync(int id, CancellationToken cancellationToken = default)
    {
        return await _context.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Id == id, cancellationToken)
            .ConfigureAwait(false);
    }

    public async Task<User?> GetByEmailAsync(
        string email, CancellationToken cancellationToken = default)
    {
        return await _context.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Email == email, cancellationToken)
            .ConfigureAwait(false);
    }

    public async Task<User> CreateAsync(
        CreateUserDto createDto, CancellationToken cancellationToken = default)
    {
        var user = new User
        {
            Email = createDto.Email,
            FullName = createDto.FullName,
            HashedPassword = BCrypt.Net.BCrypt.HashPassword(createDto.Password),
            IsActive = true,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow,
        };
        _context.Users.Add(user);
        await _context.SaveChangesAsync(cancellationToken).ConfigureAwait(false);
        return user;
    }
}
''',
}

ASPNET_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/Models/",
    "data": "backend/Data/AppDbContext.cs",
    "dto": "backend/DTOs/",
    "validators": "backend/Validators/",
    "controllers": "backend/Controllers/",
    "services": "backend/Services/",
    "middleware": "backend/Middleware/",
    "mappings": "backend/Mappings/",
    "migrations": "backend/Data/Migrations/",
    "tests": "backend/Tests/",
    "program": "backend/Program.cs",
    "config": "backend/appsettings.json",
}


ASPNET_CONFIG = FrameworkConfig(
    name="aspnet",
    display_name="ASP.NET Core",
    language="csharp",
    code_block_lang="csharp",
    error_comment_prefix="//",
    file_structure=ASPNET_FILE_STRUCTURE,
    rules=ASPNET_RULES,
    golden_examples=ASPNET_GOLDEN_EXAMPLES,
    # OCP-FIX: generation DAG moved here from shubham.py module-level dicts
    generation_order=(
        {"name": "models", "path": "backend/Models/", "task_type": "general",
         "description": "EF Core entities from architecture contract tables"},
        {"name": "dbcontext", "path": "backend/Data/AppDbContext.cs", "task_type": "general",
         "description": "Entity Framework DbContext configuration"},
        {"name": "dto", "path": "backend/DTOs/", "task_type": "general",
         "description": "DTOs with AutoMapper profiles"},
        {"name": "middleware", "path": "backend/Middleware/", "task_type": "auth_code",
         "description": "Auth middleware and JWT validation"},
        {"name": "services", "path": "backend/Services/", "task_type": "general",
         "description": "Service layer with business logic"},
        {"name": "controllers", "path": "backend/Controllers/", "task_type": "general",
         "description": "API Controllers with DI"},
        {"name": "tests", "path": "backend/Tests/", "task_type": "general",
         "description": "xUnit tests for all controllers and services"},
    ),
    dependency_graph={
        "models": set(),
        "dbcontext": {"models"},
        "dto": {"models"},
        "middleware": {"models", "dbcontext"},
        "services": {"models", "dbcontext", "dto"},
        "controllers": {"models", "dbcontext", "dto", "middleware", "services"},
        "tests": {"models", "dbcontext", "dto", "middleware", "services", "controllers"},
    },
)

register_framework(ASPNET_CONFIG)
