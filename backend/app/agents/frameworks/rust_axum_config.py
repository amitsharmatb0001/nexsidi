"""Rust Axum framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Rust + Axum + SQLx + Serde + Tokio backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

RUST_AXUM_RULES: tuple[str, ...] = (
    "1. Use Axum extractors (`Path`, `Query`, `Json`, `State`) to parse incoming requests — "
    "NEVER manually parse request bodies or query strings from raw bytes",
    "2. Every route handler MUST be an `async fn` returning `impl IntoResponse` — "
    "NEVER use synchronous blocking I/O on the Tokio runtime",
    "3. Define a single `AppError` enum implementing `IntoResponse` for all error responses — "
    "NEVER return raw `StatusCode` or string errors from handlers",
    "4. Use `axum::extract::State<Arc<AppState>>` for shared application state (DB pool, config) — "
    "NEVER use global mutable statics or `lazy_static` for runtime state",
    "5. Use SQLx compile-time checked queries with `sqlx::query!` / `sqlx::query_as!` macros — "
    "NEVER build SQL strings with `format!` or manual concatenation",
    "6. Derive `serde::Serialize` and `serde::Deserialize` on all request/response structs — "
    "use `#[serde(rename_all = \"camelCase\")]` for JSON API consistency",
    "7. Use `sqlx::PgPool` from a shared `AppState` — create the pool once in `main()` with "
    "`PgPoolOptions::new().max_connections(...)` and pass via `Router::with_state()`",
    "8. Every model struct MUST derive `sqlx::FromRow` and include `id: i64`, "
    "`created_at: chrono::DateTime<Utc>`, and `updated_at: chrono::DateTime<Utc>` columns",
    "9. Use `axum::Router::new().route(\"...\", get(handler).post(handler))` for routing — "
    "compose sub-routers with `.merge()` or `.nest()` for modular route trees",
    "10. Use `tower_http::cors::CorsLayer` and `tower_http::trace::TraceLayer` as middleware — "
    "add layers via `Router::layer()`, NEVER implement raw `Service` trait manually",
    "11. NEVER use `unwrap()` or `expect()` in handler code — propagate errors with `?` "
    "into the `AppError` type, which converts to an HTTP response automatically",
    "12. NEVER invent crate names or import paths — use ONLY types from the contract "
    "and previously generated code",
    "13. Type ALL function signatures explicitly (parameters + return types) — "
    "use `Result<Json<T>, AppError>` as the standard handler return type",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

RUST_AXUM_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
#[serde(rename_all = "camelCase")]
pub struct User {
    pub id: i64,
    pub email: String,
    pub hashed_password: String,
    pub full_name: String,
    pub is_active: bool,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
#[serde(rename_all = "camelCase")]
pub struct Product {
    pub id: i64,
    pub name: String,
    pub description: Option<String>,
    pub price: i64,
    pub owner_id: i64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateUser {
    pub email: String,
    pub full_name: String,
    pub password: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserResponse {
    pub id: i64,
    pub email: String,
    pub full_name: String,
    pub is_active: bool,
    pub created_at: DateTime<Utc>,
}

impl From<User> for UserResponse {
    fn from(u: User) -> Self {
        Self {
            id: u.id,
            email: u.email,
            full_name: u.full_name,
            is_active: u.is_active,
            created_at: u.created_at,
        }
    }
}
''',
    "handlers": '''\
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    Json,
};
use sqlx::PgPool;

use crate::error::AppError;
use crate::models::{CreateUser, User, UserResponse};
use crate::state::AppState;

pub async fn create_user(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<CreateUser>,
) -> Result<(StatusCode, Json<UserResponse>), AppError> {
    let existing = sqlx::query_as!(
        User,
        "SELECT * FROM users WHERE email = $1",
        &payload.email
    )
    .fetch_optional(&state.db)
    .await?;

    if existing.is_some() {
        return Err(AppError::Conflict("Email already registered".into()));
    }

    let hashed = crate::auth::hash_password(&payload.password)?;

    let user = sqlx::query_as!(
        User,
        r#"INSERT INTO users (email, hashed_password, full_name)
           VALUES ($1, $2, $3)
           RETURNING id, email, hashed_password, full_name,
                     is_active, created_at, updated_at"#,
        &payload.email,
        &hashed,
        &payload.full_name,
    )
    .fetch_one(&state.db)
    .await?;

    Ok((StatusCode::CREATED, Json(UserResponse::from(user))))
}

pub async fn get_user(
    State(state): State<Arc<AppState>>,
    Path(user_id): Path<i64>,
) -> Result<Json<UserResponse>, AppError> {
    let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", user_id)
        .fetch_optional(&state.db)
        .await?
        .ok_or_else(|| AppError::NotFound("User not found".into()))?;

    Ok(Json(UserResponse::from(user)))
}

pub async fn list_users(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<UserResponse>>, AppError> {
    let users = sqlx::query_as!(User, "SELECT * FROM users ORDER BY created_at DESC")
        .fetch_all(&state.db)
        .await?;

    let response: Vec<UserResponse> = users.into_iter().map(UserResponse::from).collect();
    Ok(Json(response))
}
''',
    "routes": '''\
use std::sync::Arc;

use axum::{
    routing::{get, post},
    Router,
};

use crate::handlers::users;
use crate::handlers::products;
use crate::state::AppState;

pub fn create_router(state: Arc<AppState>) -> Router {
    let user_routes = Router::new()
        .route("/", post(users::create_user).get(users::list_users))
        .route("/{id}", get(users::get_user));

    let product_routes = Router::new()
        .route("/", post(products::create_product).get(products::list_products))
        .route("/{id}", get(products::get_product).put(products::update_product).delete(products::delete_product));

    Router::new()
        .nest("/api/v1/users", user_routes)
        .nest("/api/v1/products", product_routes)
        .with_state(state)
}
''',
    "error": '''\
use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

#[derive(Debug, Serialize)]
struct ErrorBody {
    error: String,
}

#[derive(Debug)]
pub enum AppError {
    NotFound(String),
    Conflict(String),
    BadRequest(String),
    Unauthorized(String),
    Internal(String),
    Database(sqlx::Error),
}

impl From<sqlx::Error> for AppError {
    fn from(err: sqlx::Error) -> Self {
        match &err {
            sqlx::Error::RowNotFound => AppError::NotFound("Resource not found".into()),
            _ => AppError::Database(err),
        }
    }
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, message) = match self {
            AppError::NotFound(msg) => (StatusCode::NOT_FOUND, msg),
            AppError::Conflict(msg) => (StatusCode::CONFLICT, msg),
            AppError::BadRequest(msg) => (StatusCode::BAD_REQUEST, msg),
            AppError::Unauthorized(msg) => (StatusCode::UNAUTHORIZED, msg),
            AppError::Internal(msg) => (StatusCode::INTERNAL_SERVER_ERROR, msg),
            AppError::Database(err) => {
                tracing::error!("Database error: {:?}", err);
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Internal server error".into(),
                )
            }
        };

        (status, Json(ErrorBody { error: message })).into_response()
    }
}
''',
}

RUST_AXUM_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/src/models/",
    "handlers": "backend/src/handlers/",
    "routes": "backend/src/routes.rs",
    "error": "backend/src/error.rs",
    "state": "backend/src/state.rs",
    "auth": "backend/src/auth.rs",
    "main": "backend/src/main.rs",
    "migrations": "backend/migrations/",
    "config": "backend/src/config.rs",
    "tests": "backend/tests/",
}


RUST_AXUM_CONFIG = FrameworkConfig(
    name="rust_axum",
    display_name="Rust (Axum)",
    language="rust",
    code_block_lang="rust",
    error_comment_prefix="//",
    file_structure=RUST_AXUM_FILE_STRUCTURE,
    rules=RUST_AXUM_RULES,
    golden_examples=RUST_AXUM_GOLDEN_EXAMPLES,
)

register_framework(RUST_AXUM_CONFIG)
