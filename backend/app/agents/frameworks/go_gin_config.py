"""Go Gin framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Go + Gin + GORM + Air (hot reload) backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

GO_GIN_RULES: tuple[str, ...] = (
    "1. Use Gin for HTTP routing — NEVER use net/http ServeMux directly. "
    "Use `gin.Default()` or `gin.New()` with explicit middleware",
    "2. Use GORM v2 with `gorm` struct tags for all models — "
    "NEVER use raw SQL for CRUD operations. Use `gorm.Model` embedding or define "
    "ID/CreatedAt/UpdatedAt/DeletedAt fields explicitly",
    "3. Handlers MUST accept `*gin.Context` as the sole parameter — "
    "use `c.ShouldBindJSON()` for request body parsing and `c.Param()` / `c.Query()` "
    "for path and query parameters",
    "4. Return structured JSON responses with `c.JSON(statusCode, gin.H{...})` — "
    "NEVER use `fmt.Fprintf` or `c.String()` for API responses",
    "5. Use `c.AbortWithStatusJSON(statusCode, gin.H{\"error\": \"message\"})` for errors — "
    "NEVER use bare `panic()` or `log.Fatal()` in handlers",
    "6. Use Gin middleware for cross-cutting concerns — authentication, logging, CORS, "
    "and rate limiting MUST be implemented as `gin.HandlerFunc` middleware",
    "7. Use GORM AutoMigrate in development and versioned migrations in production — "
    "call `db.AutoMigrate(&Model{})` at startup for each model",
    "8. Password hashing: use `golang.org/x/crypto/bcrypt` — "
    "JWT: use `github.com/golang-jwt/jwt/v5` with HS256 and expiration claims",
    "9. Every model MUST define `ID uint` with `gorm:\"primaryKey\"`, "
    "`CreatedAt time.Time`, and `UpdatedAt time.Time` fields — "
    "use `gorm:\"uniqueIndex\"` and `gorm:\"not null\"` tags for constraints",
    "10. Route groups: use `router.Group(\"/api/v1\")` for versioning — "
    "NEVER define routes on the root engine without grouping",
    "11. NEVER use empty function bodies, `// TODO`, or `panic(\"not implemented\")` — "
    "every function must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY packages from the contract, the Go standard library, "
    "and previously generated code within the project module",
    "13. All exported functions, types, and methods MUST have proper error handling — "
    "return `error` as the last return value and ALWAYS check returned errors",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

GO_GIN_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
package models

import (
	"time"

	"gorm.io/gorm"
)

type User struct {
	ID             uint           `gorm:"primaryKey" json:"id"`
	Email          string         `gorm:"uniqueIndex;size:255;not null" json:"email"`
	HashedPassword string         `gorm:"size:255;not null" json:"-"`
	FullName       string         `gorm:"size:255;not null" json:"full_name"`
	IsActive       bool           `gorm:"default:true;not null" json:"is_active"`
	CreatedAt      time.Time      `gorm:"autoCreateTime" json:"created_at"`
	UpdatedAt      time.Time      `gorm:"autoUpdateTime" json:"updated_at"`
	DeletedAt      gorm.DeletedAt `gorm:"index" json:"-"`
	Products       []Product      `gorm:"foreignKey:OwnerID" json:"products,omitempty"`
}

type Product struct {
	ID          uint           `gorm:"primaryKey" json:"id"`
	Name        string         `gorm:"size:255;not null" json:"name"`
	Description string         `gorm:"type:text" json:"description"`
	Price       int64          `gorm:"not null" json:"price"`
	OwnerID     uint           `gorm:"not null;index" json:"owner_id"`
	Owner       User           `gorm:"constraint:OnDelete:CASCADE" json:"owner,omitempty"`
	CreatedAt   time.Time      `gorm:"autoCreateTime" json:"created_at"`
	UpdatedAt   time.Time      `gorm:"autoUpdateTime" json:"updated_at"`
	DeletedAt   gorm.DeletedAt `gorm:"index" json:"-"`
}

func Migrate(db *gorm.DB) error {
	return db.AutoMigrate(&User{}, &Product{})
}
''',
    "handlers": '''\
package handlers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"myapp/internal/models"
)

type UserHandler struct {
	DB *gorm.DB
}

func NewUserHandler(db *gorm.DB) *UserHandler {
	return &UserHandler{DB: db}
}

type CreateUserRequest struct {
	Email    string `json:"email" binding:"required,email"`
	FullName string `json:"full_name" binding:"required,min=1,max=255"`
	Password string `json:"password" binding:"required,min=8,max=128"`
}

type UserResponse struct {
	ID        uint   `json:"id"`
	Email     string `json:"email"`
	FullName  string `json:"full_name"`
	IsActive  bool   `json:"is_active"`
	CreatedAt string `json:"created_at"`
}

func toUserResponse(user *models.User) UserResponse {
	return UserResponse{
		ID:        user.ID,
		Email:     user.Email,
		FullName:  user.FullName,
		IsActive:  user.IsActive,
		CreatedAt: user.CreatedAt.Format("2006-01-02T15:04:05Z07:00"),
	}
}

func (h *UserHandler) Create(c *gin.Context) {
	var req CreateUserRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	var existing models.User
	if err := h.DB.Where("email = ?", req.Email).First(&existing).Error; err == nil {
		c.AbortWithStatusJSON(http.StatusConflict, gin.H{"error": "Email already registered"})
		return
	}

	hashedPassword, err := hashPassword(req.Password)
	if err != nil {
		c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"error": "Failed to hash password"})
		return
	}

	user := models.User{
		Email:          req.Email,
		FullName:       req.FullName,
		HashedPassword: hashedPassword,
	}
	if err := h.DB.Create(&user).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"error": "Failed to create user"})
		return
	}

	c.JSON(http.StatusCreated, gin.H{"data": toUserResponse(&user)})
}

func (h *UserHandler) GetByID(c *gin.Context) {
	idStr := c.Param("id")
	id, err := strconv.ParseUint(idStr, 10, 64)
	if err != nil {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "Invalid user ID"})
		return
	}

	var user models.User
	if err := h.DB.First(&user, id).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusNotFound, gin.H{"error": "User not found"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"data": toUserResponse(&user)})
}

func (h *UserHandler) List(c *gin.Context) {
	var users []models.User
	if err := h.DB.Find(&users).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"error": "Failed to fetch users"})
		return
	}

	response := make([]UserResponse, len(users))
	for i, user := range users {
		response[i] = toUserResponse(&user)
	}

	c.JSON(http.StatusOK, gin.H{"data": response})
}
''',
    "middleware": '''\
package middleware

import (
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
)

var jwtSecret = []byte(getEnvOrDefault("JWT_SECRET", "change-me"))

func getEnvOrDefault(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}

func HashPassword(password string) (string, error) {
	bytes, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return "", err
	}
	return string(bytes), nil
}

func CheckPassword(hashedPassword, password string) error {
	return bcrypt.CompareHashAndPassword([]byte(hashedPassword), []byte(password))
}

type Claims struct {
	UserID uint   `json:"user_id"`
	Email  string `json:"email"`
	jwt.RegisteredClaims
}

func GenerateToken(userID uint, email string) (string, error) {
	claims := Claims{
		UserID: userID,
		Email:  email,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    "nexsidi",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(24 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
		},
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(jwtSecret)
}

func RequireAuth() gin.HandlerFunc {
	return func(c *gin.Context) {
		header := c.GetHeader("Authorization")
		if header == "" || !strings.HasPrefix(header, "Bearer ") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "Missing authorization header"})
			return
		}

		tokenString := strings.TrimPrefix(header, "Bearer ")

		claims := &Claims{}
		token, err := jwt.ParseWithClaims(tokenString, claims, func(t *jwt.Token) (interface{}, error) {
			if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, jwt.ErrSignatureInvalid
			}
			return jwtSecret, nil
		})

		if err != nil || !token.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "Invalid or expired token"})
			return
		}

		c.Set("userID", claims.UserID)
		c.Set("userEmail", claims.Email)
		c.Next()
	}
}
''',
    "routes": '''\
package routes

import (
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"myapp/internal/handlers"
	"myapp/internal/middleware"
)

func SetupRouter(db *gorm.DB) *gin.Engine {
	router := gin.Default()

	router.Use(cors.New(cors.Config{
		AllowOrigins:     []string{"http://localhost:3000"},
		AllowMethods:     []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Authorization"},
		AllowCredentials: true,
	}))

	userHandler := handlers.NewUserHandler(db)
	productHandler := handlers.NewProductHandler(db)

	api := router.Group("/api/v1")
	{
		auth := api.Group("/auth")
		{
			auth.POST("/register", userHandler.Create)
			auth.POST("/login", userHandler.Login)
		}

		users := api.Group("/users")
		users.Use(middleware.RequireAuth())
		{
			users.GET("/", userHandler.List)
			users.GET("/:id", userHandler.GetByID)
		}

		products := api.Group("/products")
		products.Use(middleware.RequireAuth())
		{
			products.POST("/", productHandler.Create)
			products.GET("/", productHandler.List)
			products.GET("/:id", productHandler.GetByID)
			products.PUT("/:id", productHandler.Update)
			products.DELETE("/:id", productHandler.Delete)
		}
	}

	return router
}
''',
}

GO_GIN_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/internal/models/",
    "handlers": "backend/internal/handlers/",
    "middleware": "backend/internal/middleware/",
    "routes": "backend/internal/routes/",
    "services": "backend/internal/services/",
    "config": "backend/internal/config/",
    "database": "backend/internal/database/",
    "tests": "backend/internal/tests/",
    "seed": "backend/cmd/seed/main.go",
    "main": "backend/cmd/api/main.go",
}


GO_GIN_CONFIG = FrameworkConfig(
    name="go_gin",
    display_name="Go (Gin)",
    language="go",
    code_block_lang="go",
    error_comment_prefix="//",
    file_structure=GO_GIN_FILE_STRUCTURE,
    rules=GO_GIN_RULES,
    golden_examples=GO_GIN_GOLDEN_EXAMPLES,
)

register_framework(GO_GIN_CONFIG)
