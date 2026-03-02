"""Spring Boot framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Spring Boot + JPA + Hibernate + Spring Security backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

SPRINGBOOT_RULES: tuple[str, ...] = (
    "1. Use JPA `@Entity` with `@Table(name = \"...\")` for every entity — "
    "NEVER use JDBC templates or raw SQL for CRUD operations. "
    "Use `@Id`, `@GeneratedValue(strategy = GenerationType.IDENTITY)` for primary keys",
    "2. Use `@RestController` with `@RequestMapping(\"/api/v1/...\")` for REST endpoints — "
    "NEVER use `@Controller` with `@ResponseBody` for API controllers. "
    "Use `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping` for HTTP methods",
    "3. Use `@Service` for business logic classes — "
    "NEVER put business logic in controllers. Controllers MUST only delegate to services "
    "and return `ResponseEntity<>` objects",
    "4. Use Spring Data JPA `@Repository` interfaces extending `JpaRepository<Entity, Long>` — "
    "NEVER implement repository methods manually when Spring Data query derivation or `@Query` suffices",
    "5. Use constructor injection for all dependencies — "
    "NEVER use field injection with `@Autowired` on fields. "
    "Use `@RequiredArgsConstructor` from Lombok or explicit constructors",
    "6. Use Jakarta Validation annotations (`@Valid`, `@NotBlank`, `@Email`, `@Size`) on DTOs — "
    "NEVER validate manually in controllers. Annotate `@RequestBody` parameters with `@Valid`",
    "7. Use `ResponseEntity<T>` for all controller return types — "
    "NEVER return raw objects. Use `ResponseEntity.ok()`, `ResponseEntity.status(HttpStatus.CREATED).body()`, etc.",
    "8. Use Spring Security with `SecurityFilterChain` bean configuration — "
    "NEVER use deprecated `WebSecurityConfigurerAdapter`. "
    "Use `@EnableWebSecurity` and method-level `@PreAuthorize` for authorization",
    "9. Use `@Transactional` from `org.springframework.transaction.annotation` on service methods "
    "that perform write operations — NEVER manage transactions manually via `EntityManager`",
    "10. Every entity MUST have `@Column(name = \"created_at\")` and `@Column(name = \"updated_at\")` "
    "timestamp fields with `@CreationTimestamp` and `@UpdateTimestamp` from Hibernate",
    "11. NEVER use 'pass', '// TODO', '...', or 'throw new UnsupportedOperationException()' — "
    "every method must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY names from the contract and previously generated code. "
    "Use the `com.app` base package for all classes",
    "13. Use Lombok `@Data`, `@Builder`, `@NoArgsConstructor`, `@AllArgsConstructor` on DTOs and entities — "
    "use `@Getter` and `@Setter` on entities instead of `@Data` to avoid hashCode/equals issues with JPA",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

SPRINGBOOT_GOLDEN_EXAMPLES: dict[str, str] = {
    "entity": '''\
package com.app.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.Instant;

@Entity
@Table(name = "users")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true, length = 255)
    private String email;

    @Column(name = "hashed_password", nullable = false, length = 255)
    private String hashedPassword;

    @Column(name = "full_name", nullable = false, length = 255)
    private String fullName;

    @Column(name = "is_active", nullable = false)
    @Builder.Default
    private Boolean isActive = true;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
}
''',
    "controller": '''\
package com.app.controller;

import com.app.dto.CreateUserRequest;
import com.app.dto.UserResponse;
import com.app.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/v1/users")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    @PostMapping
    public ResponseEntity<UserResponse> createUser(@Valid @RequestBody CreateUserRequest request) {
        UserResponse created = userService.createUser(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(created);
    }

    @GetMapping("/{id}")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<UserResponse> getUserById(@PathVariable Long id) {
        UserResponse user = userService.getUserById(id);
        return ResponseEntity.ok(user);
    }

    @GetMapping
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<List<UserResponse>> getAllUsers() {
        List<UserResponse> users = userService.getAllUsers();
        return ResponseEntity.ok(users);
    }
}
''',
    "service": '''\
package com.app.service;

import com.app.dto.CreateUserRequest;
import com.app.dto.UserResponse;
import com.app.entity.User;
import com.app.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@Service
@RequiredArgsConstructor
public class UserService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    @Transactional
    public UserResponse createUser(CreateUserRequest request) {
        if (userRepository.existsByEmail(request.getEmail())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Email already registered");
        }
        User user = User.builder()
                .email(request.getEmail())
                .fullName(request.getFullName())
                .hashedPassword(passwordEncoder.encode(request.getPassword()))
                .isActive(true)
                .build();
        User saved = userRepository.save(user);
        return mapToResponse(saved);
    }

    @Transactional(readOnly = true)
    public UserResponse getUserById(Long id) {
        User user = userRepository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "User not found"));
        return mapToResponse(user);
    }

    @Transactional(readOnly = true)
    public List<UserResponse> getAllUsers() {
        return userRepository.findAll().stream()
                .map(this::mapToResponse)
                .toList();
    }

    private UserResponse mapToResponse(User user) {
        return UserResponse.builder()
                .id(user.getId())
                .email(user.getEmail())
                .fullName(user.getFullName())
                .isActive(user.getIsActive())
                .createdAt(user.getCreatedAt())
                .build();
    }
}
''',
    "repository": '''\
package com.app.repository;

import com.app.entity.User;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface UserRepository extends JpaRepository<User, Long> {

    Optional<User> findByEmail(String email);

    boolean existsByEmail(String email);

    List<User> findByIsActiveTrue();

    @Query("SELECT u FROM User u WHERE LOWER(u.fullName) LIKE LOWER(CONCAT('%', :name, '%'))")
    List<User> searchByName(String name);
}
''',
}

SPRINGBOOT_FILE_STRUCTURE: dict[str, str] = {
    "entity": "backend/src/main/java/com/app/entity/",
    "controller": "backend/src/main/java/com/app/controller/",
    "service": "backend/src/main/java/com/app/service/",
    "repository": "backend/src/main/java/com/app/repository/",
    "dto": "backend/src/main/java/com/app/dto/",
    "config": "backend/src/main/java/com/app/config/",
    "security": "backend/src/main/java/com/app/security/",
    "exception": "backend/src/main/java/com/app/exception/",
    "tests": "backend/src/test/java/com/app/",
    "resources": "backend/src/main/resources/",
    "application": "backend/src/main/java/com/app/Application.java",
}


SPRINGBOOT_CONFIG = FrameworkConfig(
    name="springboot",
    display_name="Spring Boot",
    language="java",
    code_block_lang="java",
    error_comment_prefix="//",
    file_structure=SPRINGBOOT_FILE_STRUCTURE,
    rules=SPRINGBOOT_RULES,
    golden_examples=SPRINGBOOT_GOLDEN_EXAMPLES,
    # OCP-FIX: generation DAG moved here from shubham.py module-level dicts
    generation_order=(
        {"name": "entity", "path": "backend/src/main/java/com/app/entity/", "task_type": "general",
         "description": "JPA entities from architecture contract tables"},
        {"name": "repository", "path": "backend/src/main/java/com/app/repository/", "task_type": "general",
         "description": "Spring Data JPA repositories"},
        {"name": "dto", "path": "backend/src/main/java/com/app/dto/", "task_type": "general",
         "description": "DTOs with validation annotations"},
        {"name": "security", "path": "backend/src/main/java/com/app/security/", "task_type": "auth_code",
         "description": "Spring Security configuration and JWT handling"},
        {"name": "service", "path": "backend/src/main/java/com/app/service/", "task_type": "general",
         "description": "Service layer with business logic"},
        {"name": "controller", "path": "backend/src/main/java/com/app/controller/", "task_type": "general",
         "description": "REST controllers using services and DTOs"},
        {"name": "tests", "path": "backend/src/test/java/com/app/", "task_type": "general",
         "description": "JUnit tests for all endpoints and services"},
    ),
    dependency_graph={
        "entity": set(),
        "repository": {"entity"},
        "dto": {"entity"},
        "security": {"entity", "repository"},
        "service": {"entity", "repository", "dto"},
        "controller": {"entity", "repository", "dto", "security", "service"},
        "tests": {"entity", "repository", "dto", "security", "service", "controller"},
    },
)

register_framework(SPRINGBOOT_CONFIG)
