use axum::{
    extract::{Multipart, State},
    http::StatusCode,
    Json,
};
use chrono::{DateTime, Utc};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::AppState;

const MAX_FILE_SIZE: usize = 5 * 1024 * 1024; // 5 MB
const BCRYPT_COST: u32 = 12;
const UPLOADS_DIR: &str = "/uploads";

type ApiError = (StatusCode, Json<Value>);
type ApiResult = Result<(StatusCode, Json<Value>), ApiError>;

#[derive(sqlx::FromRow)]
struct UserRow {
    id: Uuid,
    email: String,
    username: String,
    first_name: String,
    last_name: String,
    profile_picture_url: Option<String>,
    preferred_language: String,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

fn api_err(status: StatusCode, code: &str, message: &str) -> ApiError {
    (status, Json(json!({"error": code, "message": message})))
}

fn validation_err(fields: serde_json::Map<String, Value>) -> ApiError {
    (
        StatusCode::UNPROCESSABLE_ENTITY,
        Json(json!({"error": "validation_error", "fields": fields})),
    )
}

pub async fn register(
    State(state): State<AppState>,
    mut multipart: Multipart,
) -> ApiResult {
    let pool = &state.db;
    // ── 1. Parse multipart fields ─────────────────────────────────────────
    let mut text_fields: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    let mut file_bytes: Option<Vec<u8>> = None;

    while let Some(field) = multipart.next_field().await.map_err(|e| {
        api_err(StatusCode::BAD_REQUEST, "invalid_multipart", &e.to_string())
    })? {
        let name = field.name().unwrap_or("").to_string();

        if name == "profile_picture" && field.file_name().is_some() {
            // ── File upload ──────────────────────────────────────────────
            let raw = field
                .bytes()
                .await
                .map_err(|e| api_err(StatusCode::BAD_REQUEST, "read_error", &e.to_string()))?;

            if raw.len() > MAX_FILE_SIZE {
                return Err(api_err(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    "file_too_large",
                    "File exceeds 5MB limit",
                ));
            }
            file_bytes = Some(raw.to_vec());
        } else {
            let text = field
                .text()
                .await
                .map_err(|e| api_err(StatusCode::BAD_REQUEST, "read_error", &e.to_string()))?;
            text_fields.insert(name, text);
        }
    }

    let email = text_fields.remove("email").unwrap_or_default();
    let username = text_fields.remove("username").unwrap_or_default();
    let first_name = text_fields.remove("first_name").unwrap_or_default();
    let last_name = text_fields.remove("last_name").unwrap_or_default();
    let password = text_fields.remove("password").unwrap_or_default();
    let profile_picture_url_text = text_fields.remove("profile_picture_url");

    // ── 2. Validate MIME type first (before field validation) ─────────────
    let saved_picture_url: Option<String> = if let Some(ref bytes) = file_bytes {
        let kind = infer::get(bytes);
        let ext = match kind.map(|k| k.mime_type()) {
            Some("image/jpeg") => "jpg",
            Some("image/png") => "png",
            Some("image/webp") => "webp",
            _ => {
                return Err((
                    StatusCode::UNPROCESSABLE_ENTITY,
                    Json(json!({
                        "error": "validation_error",
                        "fields": {"profile_picture": "Only JPEG, PNG, and WebP images are allowed"}
                    })),
                ));
            }
        };

        tokio::fs::create_dir_all(UPLOADS_DIR)
            .await
            .map_err(|e| api_err(StatusCode::INTERNAL_SERVER_ERROR, "upload_failed", &e.to_string()))?;

        let filename = format!("{}.{}", Uuid::new_v4(), ext);
        let path = format!("{}/{}", UPLOADS_DIR, &filename);
        tokio::fs::write(&path, bytes)
            .await
            .map_err(|e| api_err(StatusCode::INTERNAL_SERVER_ERROR, "upload_failed", &e.to_string()))?;

        Some(format!("/uploads/{}", filename))
    } else {
        profile_picture_url_text.filter(|u| !u.is_empty())
    };

    // ── 3. Field validation ───────────────────────────────────────────────
    let mut errors = serde_json::Map::new();

    if !is_valid_email(&email) {
        errors.insert("email".into(), json!("Invalid email format"));
    }

    if username.len() < 3 || username.len() > 20 {
        errors.insert(
            "username".into(),
            json!("Username must be between 3 and 20 characters"),
        );
    } else if !username.chars().all(|c| c.is_alphanumeric() || c == '_') {
        errors.insert(
            "username".into(),
            json!("Username may only contain letters, numbers, and underscores"),
        );
    }

    if first_name.is_empty() || first_name.len() > 50 {
        errors.insert(
            "first_name".into(),
            json!("First name must be between 1 and 50 characters"),
        );
    }

    if last_name.is_empty() || last_name.len() > 50 {
        errors.insert(
            "last_name".into(),
            json!("Last name must be between 1 and 50 characters"),
        );
    }

    if password.len() < 8 {
        errors.insert(
            "password".into(),
            json!("Password must be at least 8 characters"),
        );
    } else if !password.chars().any(|c| c.is_uppercase()) {
        errors.insert(
            "password".into(),
            json!("Password must contain at least one uppercase letter"),
        );
    } else if !password.chars().any(|c| c.is_lowercase()) {
        errors.insert(
            "password".into(),
            json!("Password must contain at least one lowercase letter"),
        );
    } else if !password.chars().any(|c| c.is_ascii_digit()) {
        errors.insert(
            "password".into(),
            json!("Password must contain at least one number"),
        );
    }

    if !errors.is_empty() {
        return Err(validation_err(errors));
    }

    // ── 4. Hash password (blocking – CPU-intensive) ───────────────────────
    let password_hash = tokio::task::spawn_blocking(move || bcrypt::hash(&password, BCRYPT_COST))
        .await
        .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "hashing_failed", ""))?
        .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "hashing_failed", ""))?;

    // ── 5. Insert into database ───────────────────────────────────────────
    let user = sqlx::query_as::<_, UserRow>(
        r#"
        INSERT INTO users (email, username, first_name, last_name, password_hash, profile_picture_url)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, email, username, first_name, last_name,
                  profile_picture_url, preferred_language, created_at, updated_at
        "#,
    )
    .bind(&email)
    .bind(&username)
    .bind(&first_name)
    .bind(&last_name)
    .bind(&password_hash)
    .bind(&saved_picture_url)
    .fetch_one(pool)
    .await
    .map_err(|e| {
        if let sqlx::Error::Database(db_err) = &e {
            if let Some(constraint) = db_err.constraint() {
                if constraint == "users_email_key" {
                    return (
                        StatusCode::CONFLICT,
                        Json(json!({"error": "conflict", "field": "email",
                                   "message": "Email already taken"})),
                    );
                }
                if constraint == "users_username_key" {
                    return (
                        StatusCode::CONFLICT,
                        Json(json!({"error": "conflict", "field": "username",
                                   "message": "Username already taken"})),
                    );
                }
            }
        }
        api_err(StatusCode::INTERNAL_SERVER_ERROR, "database_error", &e.to_string())
    })?;

    // ── 6. Return 201 – never expose password_hash ────────────────────────
    Ok((
        StatusCode::CREATED,
        Json(json!({
            "id":                  user.id,
            "email":               user.email,
            "username":            user.username,
            "first_name":          user.first_name,
            "last_name":           user.last_name,
            "profile_picture_url": user.profile_picture_url,
            "preferred_language":  user.preferred_language,
            "created_at":          user.created_at,
            "updated_at":          user.updated_at,
        })),
    ))
}

fn is_valid_email(email: &str) -> bool {
    let mut parts = email.splitn(2, '@');
    let local = parts.next().unwrap_or("");
    let domain = parts.next().unwrap_or("");
    !local.is_empty() && !domain.is_empty() && domain.contains('.')
}
