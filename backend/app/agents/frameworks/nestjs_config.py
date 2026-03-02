"""NestJS framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
NestJS + TypeORM + class-validator + Passport backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

NESTJS_RULES: tuple[str, ...] = (
    "1. Use TypeORM for database access — NEVER use Prisma, Drizzle, or Sequelize. "
    "Define entities with `@Entity()` decorator and extend `BaseEntity` or use Repository pattern",
    "2. Use class-validator for request validation — NEVER use Zod, Joi, or express-validator. "
    "Decorate DTO properties with `@IsString()`, `@IsEmail()`, `@MinLength()`, etc.",
    "3. Use class-transformer with `@Transform()` and `@Exclude()` for response serialization — "
    "enable `ClassSerializerInterceptor` globally, NEVER manually strip fields",
    "4. Use NestJS Modules to organize features — every feature gets its own module with "
    "`@Module({ imports, controllers, providers, exports })`. NEVER put logic in AppModule",
    "5. Use `@Injectable()` services for business logic — controllers MUST delegate to services. "
    "NEVER put database queries or business rules directly in controllers",
    "6. Use Passport + `@nestjs/passport` for authentication — implement strategies with "
    "`PassportStrategy(Strategy)`. Use `@UseGuards(AuthGuard('jwt'))` on protected routes",
    "7. Use NestJS Pipes for validation — enable `ValidationPipe` globally with "
    "`whitelist: true, forbidNonWhitelisted: true, transform: true`",
    "8. Use NestJS Guards for authorization — create custom guards implementing `CanActivate`. "
    "NEVER check permissions inside controller methods directly",
    "9. TypeORM repositories: use `@InjectRepository(Entity)` in services — "
    "NEVER use raw SQL strings. Use `repository.find()`, `repository.findOne()`, "
    "`repository.save()`, `repository.createQueryBuilder()`",
    "10. Every entity MUST have `@CreateDateColumn()` and `@UpdateDateColumn()` — "
    "use `@PrimaryGeneratedColumn('uuid')` for primary keys unless numeric IDs are required",
    "11. Use ESM-compatible imports — NEVER use `require()`. "
    "All files use `.ts` extension. Use barrel `index.ts` exports per feature module",
    "12. NEVER use 'any' type — use proper TypeScript types and interfaces. "
    "NEVER use `// TODO`, `// FIXME`, or empty method bodies",
    "13. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use `@app/` path alias for project-internal imports",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

NESTJS_GOLDEN_EXAMPLES: dict[str, str] = {
    "entity": '''\
import {
  Column,
  CreateDateColumn,
  Entity,
  ManyToOne,
  OneToMany,
  PrimaryGeneratedColumn,
  UpdateDateColumn,
} from "typeorm";

@Entity("users")
export class User {
  @PrimaryGeneratedColumn("uuid")
  id: string;

  @Column({ type: "varchar", length: 255, unique: true })
  email: string;

  @Column({ type: "varchar", length: 255 })
  hashedPassword: string;

  @Column({ type: "varchar", length: 255 })
  fullName: string;

  @Column({ type: "boolean", default: true })
  isActive: boolean;

  @CreateDateColumn({ type: "timestamptz" })
  createdAt: Date;

  @UpdateDateColumn({ type: "timestamptz" })
  updatedAt: Date;

  @OneToMany(() => Product, (product) => product.owner)
  products: Product[];
}

@Entity("products")
export class Product {
  @PrimaryGeneratedColumn("uuid")
  id: string;

  @Column({ type: "varchar", length: 255 })
  name: string;

  @Column({ type: "text", nullable: true })
  description: string | null;

  @Column({ type: "int" })
  price: number;

  @ManyToOne(() => User, (user) => user.products, { onDelete: "CASCADE" })
  owner: User;

  @Column({ type: "uuid" })
  ownerId: string;

  @CreateDateColumn({ type: "timestamptz" })
  createdAt: Date;

  @UpdateDateColumn({ type: "timestamptz" })
  updatedAt: Date;
}
''',
    "dto": '''\
import { Exclude, Expose } from "class-transformer";
import {
  IsEmail,
  IsInt,
  IsNotEmpty,
  IsOptional,
  IsPositive,
  IsString,
  MaxLength,
  MinLength,
} from "class-validator";

export class CreateUserDto {
  @IsEmail()
  email: string;

  @IsString()
  @IsNotEmpty()
  @MaxLength(255)
  fullName: string;

  @IsString()
  @MinLength(8)
  @MaxLength(128)
  password: string;
}

export class LoginDto {
  @IsEmail()
  email: string;

  @IsString()
  @IsNotEmpty()
  password: string;
}

export class CreateProductDto {
  @IsString()
  @IsNotEmpty()
  @MaxLength(255)
  name: string;

  @IsString()
  @IsOptional()
  description?: string;

  @IsInt()
  @IsPositive()
  price: number;
}

export class UserResponseDto {
  @Expose()
  id: string;

  @Expose()
  email: string;

  @Expose()
  fullName: string;

  @Expose()
  isActive: boolean;

  @Expose()
  createdAt: Date;

  @Exclude()
  hashedPassword: string;

  @Exclude()
  updatedAt: Date;
}
''',
    "controller": '''\
import {
  Body,
  ClassSerializerInterceptor,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Req,
  UseGuards,
  UseInterceptors,
} from "@nestjs/common";
import { AuthGuard } from "@nestjs/passport";
import { Request } from "express";
import { CreateProductDto } from "@app/dto/create-product.dto";
import { Product } from "@app/entities/product.entity";
import { ProductService } from "@app/services/product.service";

interface AuthenticatedRequest extends Request {
  user: { id: string; email: string };
}

@Controller("products")
@UseInterceptors(ClassSerializerInterceptor)
@UseGuards(AuthGuard("jwt"))
export class ProductController {
  constructor(private readonly productService: ProductService) {}

  @Post()
  async create(
    @Body() dto: CreateProductDto,
    @Req() req: AuthenticatedRequest,
  ): Promise<Product> {
    return this.productService.create(dto, req.user.id);
  }

  @Get()
  async findAll(@Req() req: AuthenticatedRequest): Promise<Product[]> {
    return this.productService.findAllByOwner(req.user.id);
  }

  @Get(":id")
  async findOne(
    @Param("id", ParseUUIDPipe) id: string,
    @Req() req: AuthenticatedRequest,
  ): Promise<Product> {
    return this.productService.findOneByOwner(id, req.user.id);
  }

  @Patch(":id")
  async update(
    @Param("id", ParseUUIDPipe) id: string,
    @Body() dto: Partial<CreateProductDto>,
    @Req() req: AuthenticatedRequest,
  ): Promise<Product> {
    return this.productService.update(id, dto, req.user.id);
  }

  @Delete(":id")
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(
    @Param("id", ParseUUIDPipe) id: string,
    @Req() req: AuthenticatedRequest,
  ): Promise<void> {
    await this.productService.remove(id, req.user.id);
  }
}
''',
    "service": '''\
import {
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import { InjectRepository } from "@nestjs/typeorm";
import * as argon2 from "argon2";
import { Repository } from "typeorm";
import { CreateUserDto } from "@app/dto/create-user.dto";
import { User } from "@app/entities/user.entity";

@Injectable()
export class UserService {
  constructor(
    @InjectRepository(User)
    private readonly userRepository: Repository<User>,
  ) {}

  async create(dto: CreateUserDto): Promise<User> {
    const existing = await this.userRepository.findOne({
      where: { email: dto.email },
    });
    if (existing) {
      throw new ConflictException("Email already registered");
    }

    const user = this.userRepository.create({
      email: dto.email,
      fullName: dto.fullName,
      hashedPassword: await argon2.hash(dto.password),
    });

    return this.userRepository.save(user);
  }

  async findById(id: string): Promise<User> {
    const user = await this.userRepository.findOne({ where: { id } });
    if (!user) {
      throw new NotFoundException(`User with id "${id}" not found`);
    }
    return user;
  }

  async findByEmail(email: string): Promise<User | null> {
    return this.userRepository.findOne({ where: { email } });
  }

  async validateCredentials(email: string, password: string): Promise<User | null> {
    const user = await this.findByEmail(email);
    if (!user) {
      return null;
    }

    const isValid = await argon2.verify(user.hashedPassword, password);
    if (!isValid) {
      return null;
    }

    return user;
  }

  async deactivate(id: string): Promise<User> {
    const user = await this.findById(id);
    user.isActive = false;
    return this.userRepository.save(user);
  }
}
''',
}

NESTJS_FILE_STRUCTURE: dict[str, str] = {
    "entity": "backend/src/entities/",
    "dto": "backend/src/dto/",
    "controller": "backend/src/controllers/",
    "service": "backend/src/services/",
    "module": "backend/src/modules/",
    "guard": "backend/src/guards/",
    "strategy": "backend/src/strategies/",
    "pipe": "backend/src/pipes/",
    "tests": "backend/src/__tests__/",
    "main": "backend/src/main.ts",
    "app_module": "backend/src/app.module.ts",
}


NESTJS_CONFIG = FrameworkConfig(
    name="nestjs",
    display_name="NestJS",
    language="typescript",
    code_block_lang="typescript",
    error_comment_prefix="//",
    file_structure=NESTJS_FILE_STRUCTURE,
    rules=NESTJS_RULES,
    golden_examples=NESTJS_GOLDEN_EXAMPLES,
    # OCP-FIX: generation DAG moved here from shubham.py module-level dicts
    generation_order=(
        {"name": "entity", "path": "backend/src/entities/", "task_type": "general",
         "description": "TypeORM entities from architecture contract tables"},
        {"name": "dto", "path": "backend/src/dto/", "task_type": "general",
         "description": "class-validator DTOs matching entities"},
        {"name": "guards", "path": "backend/src/guards/auth.guard.ts", "task_type": "auth_code",
         "description": "NestJS Guards for authentication and authorization"},
        {"name": "controllers", "path": "backend/src/controllers/", "task_type": "general",
         "description": "NestJS Controllers with dependency injection"},
        {"name": "services", "path": "backend/src/services/", "task_type": "general",
         "description": "NestJS Services with business logic"},
        {"name": "modules", "path": "backend/src/modules/", "task_type": "general",
         "description": "NestJS Modules wiring controllers, services, entities"},
        {"name": "tests", "path": "backend/src/__tests__/", "task_type": "general",
         "description": "Jest tests for all controllers and services"},
        {"name": "seed", "path": "backend/src/scripts/seed.ts", "task_type": "general",
         "description": "Database seed script for development"},
    ),
    dependency_graph={
        "entity": set(),
        "dto": {"entity"},
        "guards": {"entity", "dto"},
        "controllers": {"entity", "dto", "guards"},
        "services": {"entity", "dto", "guards"},
        "modules": {"entity", "dto", "guards", "controllers", "services"},
        "tests": {"entity", "dto", "guards", "controllers", "services", "modules"},
        "seed": {"entity"},
    },
)

register_framework(NESTJS_CONFIG)
