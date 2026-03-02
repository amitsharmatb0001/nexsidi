"""Kotlin Ktor framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Kotlin + Ktor + Exposed + kotlinx.serialization backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

KOTLIN_KTOR_RULES: tuple[str, ...] = (
    "1. Use Ktor routing DSL for endpoint definitions — NEVER use annotation-based routing. "
    "Define routes inside `fun Application.configureRouting()` or `fun Route.userRoutes()` extension functions",
    "2. Use Exposed ORM for database access — NEVER use Hibernate, jOOQ, or raw JDBC. "
    "Define tables as `object ... : LongIdTable()` and entities as `class ... : LongEntity()`",
    "3. Use kotlinx.serialization with `@Serializable` data classes for request/response DTOs — "
    "NEVER use Jackson, Gson, or Moshi. Install `ContentNegotiation` plugin with `json()` format",
    "4. Use Ktor StatusPages plugin for centralized error handling — "
    "register `exception<Throwable>` handlers, NEVER use try/catch in route handlers for HTTP errors",
    "5. Use `newSuspendedTransaction {}` from Exposed for coroutine-safe database operations — "
    "NEVER use `transaction {}` in suspend functions, it blocks the coroutine dispatcher",
    "6. Use Ktor `Authentication` plugin with JWT — configure `jwt` authentication provider, "
    "validate claims in `validate {}` block, access principal via `call.principal<JWTPrincipal>()`",
    "7. Use constructor injection for service dependencies — pass repositories and services as "
    "constructor parameters, NEVER use global singletons or service locators",
    "8. Use Kotlin coroutines throughout — all route handlers and service methods MUST be suspend functions. "
    "NEVER use blocking I/O on the main dispatcher",
    "9. Every Exposed table MUST define an `id` column (via `LongIdTable`), "
    "`createdAt` with `datetime(\"created_at\").defaultExpression(CurrentDateTime)`, and "
    "`updatedAt` with `datetime(\"updated_at\").defaultExpression(CurrentDateTime)`",
    "10. Use Ktor `call.receive<T>()` to deserialize request bodies and `call.respond(status, body)` "
    "for responses — NEVER manually parse JSON or set response headers for content type",
    "11. NEVER use placeholder implementations — no `TODO()`, `throw NotImplementedError()`, "
    "empty function bodies, or `// TODO` comments. Every function must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use the project package structure `com.app.*`",
    "13. Type declarations on EVERY function (parameters + return types) — "
    "use `List<Model>`, nullable types `Model?`, NEVER use `Any` as a substitute for proper types",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

KOTLIN_KTOR_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
package com.app.models

import org.jetbrains.exposed.dao.LongEntity
import org.jetbrains.exposed.dao.LongEntityClass
import org.jetbrains.exposed.dao.id.LongIdTable
import org.jetbrains.exposed.sql.kotlin.datetime.CurrentDateTime
import org.jetbrains.exposed.sql.kotlin.datetime.datetime

object Users : LongIdTable("users") {
    val email = varchar("email", 255).uniqueIndex()
    val hashedPassword = varchar("hashed_password", 255)
    val fullName = varchar("full_name", 255)
    val isActive = bool("is_active").default(true)
    val createdAt = datetime("created_at").defaultExpression(CurrentDateTime)
    val updatedAt = datetime("updated_at").defaultExpression(CurrentDateTime)
}

class User(id: org.jetbrains.exposed.dao.id.EntityID<Long>) : LongEntity(id) {
    companion object : LongEntityClass<User>(Users)

    var email by Users.email
    var hashedPassword by Users.hashedPassword
    var fullName by Users.fullName
    var isActive by Users.isActive
    var createdAt by Users.createdAt
    var updatedAt by Users.updatedAt
    val products by Product referrersOn Products.ownerId
}

object Products : LongIdTable("products") {
    val name = varchar("name", 255)
    val description = text("description").nullable()
    val price = long("price")
    val ownerId = reference("owner_id", Users)
    val createdAt = datetime("created_at").defaultExpression(CurrentDateTime)
    val updatedAt = datetime("updated_at").defaultExpression(CurrentDateTime)
}

class Product(id: org.jetbrains.exposed.dao.id.EntityID<Long>) : LongEntity(id) {
    companion object : LongEntityClass<Product>(Products)

    var name by Products.name
    var description by Products.description
    var price by Products.price
    var ownerId by Products.ownerId
    var owner by User referencedOn Products.ownerId
    var createdAt by Products.createdAt
    var updatedAt by Products.updatedAt
}
''',
    "routes": '''\
package com.app.routes

import com.app.dto.CreateUserRequest
import com.app.dto.UserResponse
import com.app.services.UserService
import io.ktor.http.*
import io.ktor.server.auth.*
import io.ktor.server.auth.jwt.*
import io.ktor.server.request.*
import io.ktor.server.response.*
import io.ktor.server.routing.*

fun Route.userRoutes(userService: UserService) {
    route("/api/users") {
        post {
            val request = call.receive<CreateUserRequest>()
            val existing = userService.getByEmail(request.email)
            if (existing != null) {
                call.respond(HttpStatusCode.Conflict, mapOf("error" to "Email already registered"))
                return@post
            }
            val user = userService.create(request)
            call.respond(HttpStatusCode.Created, user)
        }

        get {
            val users = userService.listAll()
            call.respond(HttpStatusCode.OK, users)
        }

        get("/{id}") {
            val id = call.parameters["id"]?.toLongOrNull()
                ?: return@get call.respond(HttpStatusCode.BadRequest, mapOf("error" to "Invalid user ID"))
            val user = userService.getById(id)
                ?: return@get call.respond(HttpStatusCode.NotFound, mapOf("error" to "User not found"))
            call.respond(HttpStatusCode.OK, user)
        }

        authenticate("auth-jwt") {
            get("/me") {
                val principal = call.principal<JWTPrincipal>()
                    ?: return@get call.respond(HttpStatusCode.Unauthorized, mapOf("error" to "Invalid token"))
                val userId = principal.payload.getClaim("userId").asLong()
                val user = userService.getById(userId)
                    ?: return@get call.respond(HttpStatusCode.NotFound, mapOf("error" to "User not found"))
                call.respond(HttpStatusCode.OK, user)
            }
        }
    }
}
''',
    "services": '''\
package com.app.services

import com.app.dto.CreateUserRequest
import com.app.dto.UserResponse
import com.app.repositories.UserRepository
import at.favre.lib.crypto.bcrypt.BCrypt

class UserService(private val userRepository: UserRepository) {

    suspend fun getById(id: Long): UserResponse? {
        return userRepository.findById(id)?.toResponse()
    }

    suspend fun getByEmail(email: String): UserResponse? {
        return userRepository.findByEmail(email)?.toResponse()
    }

    suspend fun listAll(): List<UserResponse> {
        return userRepository.findAll().map { it.toResponse() }
    }

    suspend fun create(request: CreateUserRequest): UserResponse {
        val hashedPassword = BCrypt.withDefaults().hashToString(12, request.password.toCharArray())
        return userRepository.create(
            email = request.email,
            fullName = request.fullName,
            hashedPassword = hashedPassword,
        ).toResponse()
    }

    suspend fun authenticate(email: String, password: String): UserResponse? {
        val user = userRepository.findByEmail(email) ?: return null
        val result = BCrypt.verifyer().verify(password.toCharArray(), user.hashedPassword)
        if (!result.verified) return null
        return user.toResponse()
    }

    private fun com.app.models.User.toResponse(): UserResponse {
        return UserResponse(
            id = this.id.value,
            email = this.email,
            fullName = this.fullName,
            isActive = this.isActive,
            createdAt = this.createdAt.toString(),
        )
    }
}
''',
    "repositories": '''\
package com.app.repositories

import com.app.models.Product
import com.app.models.Products
import com.app.models.User
import com.app.models.Users
import org.jetbrains.exposed.sql.SortOrder
import org.jetbrains.exposed.sql.transactions.experimental.newSuspendedTransaction

class UserRepository {

    suspend fun findById(id: Long): User? = newSuspendedTransaction {
        User.findById(id)
    }

    suspend fun findByEmail(email: String): User? = newSuspendedTransaction {
        User.find { Users.email eq email }.firstOrNull()
    }

    suspend fun findAll(): List<User> = newSuspendedTransaction {
        User.all().orderBy(Users.createdAt to SortOrder.DESC).toList()
    }

    suspend fun create(email: String, fullName: String, hashedPassword: String): User = newSuspendedTransaction {
        User.new {
            this.email = email
            this.fullName = fullName
            this.hashedPassword = hashedPassword
        }
    }

    suspend fun update(id: Long, block: User.() -> Unit): User? = newSuspendedTransaction {
        val user = User.findById(id) ?: return@newSuspendedTransaction null
        user.apply(block)
        user
    }

    suspend fun delete(id: Long): Boolean = newSuspendedTransaction {
        val user = User.findById(id) ?: return@newSuspendedTransaction false
        user.delete()
        true
    }
}

class ProductRepository {

    suspend fun findById(id: Long): Product? = newSuspendedTransaction {
        Product.findById(id)
    }

    suspend fun findByOwnerId(ownerId: Long): List<Product> = newSuspendedTransaction {
        Product.find { Products.ownerId eq ownerId }
            .orderBy(Products.createdAt to SortOrder.DESC)
            .toList()
    }

    suspend fun findAll(): List<Product> = newSuspendedTransaction {
        Product.all().orderBy(Products.createdAt to SortOrder.DESC).toList()
    }

    suspend fun create(
        name: String,
        description: String?,
        price: Long,
        ownerId: Long,
    ): Product = newSuspendedTransaction {
        Product.new {
            this.name = name
            this.description = description
            this.price = price
            this.ownerId = org.jetbrains.exposed.dao.id.EntityID(ownerId, Users)
        }
    }

    suspend fun delete(id: Long): Boolean = newSuspendedTransaction {
        val product = Product.findById(id) ?: return@newSuspendedTransaction false
        product.delete()
        true
    }
}
''',
}

KOTLIN_KTOR_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/src/main/kotlin/com/app/models/",
    "dto": "backend/src/main/kotlin/com/app/dto/",
    "routes": "backend/src/main/kotlin/com/app/routes/",
    "services": "backend/src/main/kotlin/com/app/services/",
    "repositories": "backend/src/main/kotlin/com/app/repositories/",
    "plugins": "backend/src/main/kotlin/com/app/plugins/",
    "config": "backend/src/main/kotlin/com/app/config/",
    "tests": "backend/src/test/kotlin/com/app/",
    "main": "backend/src/main/kotlin/com/app/Application.kt",
    "build": "backend/build.gradle.kts",
}


KOTLIN_KTOR_CONFIG = FrameworkConfig(
    name="kotlin_ktor",
    display_name="Kotlin (Ktor)",
    language="kotlin",
    code_block_lang="kotlin",
    error_comment_prefix="//",
    file_structure=KOTLIN_KTOR_FILE_STRUCTURE,
    rules=KOTLIN_KTOR_RULES,
    golden_examples=KOTLIN_KTOR_GOLDEN_EXAMPLES,
    # OCP-FIX: generation DAG moved here from shubham.py module-level dicts
    generation_order=(
        {"name": "models", "path": "backend/src/main/kotlin/com/app/models/", "task_type": "general",
         "description": "Exposed tables and entity classes"},
        {"name": "dto", "path": "backend/src/main/kotlin/com/app/dto/", "task_type": "general",
         "description": "kotlinx.serialization data classes"},
        {"name": "auth", "path": "backend/src/main/kotlin/com/app/plugins/Authentication.kt", "task_type": "auth_code",
         "description": "Ktor JWT authentication plugin configuration"},
        {"name": "routes", "path": "backend/src/main/kotlin/com/app/routes/", "task_type": "general",
         "description": "Ktor routing DSL with request handling"},
        {"name": "services", "path": "backend/src/main/kotlin/com/app/services/", "task_type": "general",
         "description": "Business logic services with Exposed transactions"},
        {"name": "repository", "path": "backend/src/main/kotlin/com/app/repository/", "task_type": "general",
         "description": "Exposed DAO repository pattern"},
        {"name": "tests", "path": "backend/src/test/kotlin/com/app/", "task_type": "general",
         "description": "Ktor test engine tests for all routes"},
    ),
    dependency_graph={
        "models": set(),
        "dto": {"models"},
        "auth": {"models", "dto"},
        "routes": {"models", "dto", "auth"},
        "services": {"models", "dto", "auth"},
        "repository": {"models", "dto"},
        "tests": {"models", "dto", "auth", "routes", "services", "repository"},
    },
)

register_framework(KOTLIN_KTOR_CONFIG)
