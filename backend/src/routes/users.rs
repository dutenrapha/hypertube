use axum::{
    extract::{Multipart, Path, State},
    http::{HeaderMap, StatusCode},
    Json,
};
use serde_json::{json, Value};
use sqlx::Row;
use uuid::Uuid;

use crate::{jwt, AppState};

const MAX_FILE_SIZE: usize = 5 * 1024 * 1024;
const UPLOADS_DIR: &str = "/uploads";

type ApiError = (StatusCode, Json<Value>);
type ApiResult = Result<(StatusCode, Json<Value>), ApiError>;

fn api_err(status: StatusCode, code: &str, msg: &str) -> ApiError {
    (status, Json(json!({"error": code, "message": msg})))
}

fn is_valid_email(email: &str) -> bool {
    let mut parts = email.splitn(2, '@');
    let local = parts.next().unwrap_or("");
    let domain = parts.next().unwrap_or("");
    !local.is_empty() && !domain.is_empty() && domain.contains('.')
}

// ── GET /api/users/:id ────────────────────────────────────────────────────────
// Returns user; includes email only when requesting own profile. Requires authentication.
pub async fn get_user(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
) -> ApiResult {
    jwt::verify_from_headers(&headers)?;

    let row = sqlx::query(
        "SELECT id, username, first_name, last_name, profile_picture_url, preferred_language \
         FROM users WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.db)
    .await
    .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "db_error", ""))?
    .ok_or_else(|| api_err(StatusCode::NOT_FOUND, "not_found", "User not found"))?;

    Ok((StatusCode::OK, Json(json!({
        "id":                  row.try_get::<Uuid, _>("id").unwrap().to_string(),
        "username":            row.try_get::<String, _>("username").unwrap(),
        "first_name":          row.try_get::<String, _>("first_name").unwrap(),
        "last_name":           row.try_get::<String, _>("last_name").unwrap(),
        "profile_picture_url": row.try_get::<Option<String>, _>("profile_picture_url").unwrap(),
        "preferred_language":  row.try_get::<String, _>("preferred_language").unwrap(),
    }))))
}

// ── PATCH /api/users/:id ──────────────────────────────────────────────────────
// Update own profile. Requires authentication; only the same user may edit.
// Accepts multipart/form-data with optional fields:
//   email, username, first_name, last_name, password, preferred_language,
//   profile_picture (file)
pub async fn update_user(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
    mut multipart: Multipart,
) -> ApiResult {
    let claims = jwt::verify_from_headers(&headers)?;

    // Only the user themselves can update their profile
    if claims.user_id != id.to_string() {
        return Err(api_err(
            StatusCode::FORBIDDEN,
            "forbidden",
            "Cannot update another user's profile",
        ));
    }

    // ── Parse multipart ──────────────────────────────────────────────────────
    let mut fields: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    let mut file_bytes: Option<Vec<u8>> = None;

    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|e| api_err(StatusCode::BAD_REQUEST, "invalid_multipart", &e.to_string()))?
    {
        let name = field.name().unwrap_or("").to_string();
        if name == "profile_picture" && field.file_name().is_some() {
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
            fields.insert(name, text);
        }
    }

    // ── Handle profile picture ───────────────────────────────────────────────
    let new_picture_url: Option<String> = if let Some(ref bytes) = file_bytes {
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
        tokio::fs::create_dir_all(UPLOADS_DIR).await.map_err(|e| {
            api_err(StatusCode::INTERNAL_SERVER_ERROR, "upload_failed", &e.to_string())
        })?;
        let filename = format!("{}.{}", Uuid::new_v4(), ext);
        let path = format!("{}/{}", UPLOADS_DIR, &filename);
        tokio::fs::write(&path, bytes).await.map_err(|e| {
            api_err(StatusCode::INTERNAL_SERVER_ERROR, "upload_failed", &e.to_string())
        })?;
        Some(format!("/uploads/{}", filename))
    } else {
        None
    };

    // ── Validate supplied fields ─────────────────────────────────────────────
    let mut errors = serde_json::Map::new();

    if let Some(email) = fields.get("email") {
        if !email.is_empty() && !is_valid_email(email) {
            errors.insert("email".into(), json!("Invalid email format"));
        }
    }

    if let Some(username) = fields.get("username") {
        if !username.is_empty() {
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
        }
    }

    if let Some(first_name) = fields.get("first_name") {
        if !first_name.is_empty() && first_name.len() > 50 {
            errors.insert(
                "first_name".into(),
                json!("First name must be between 1 and 50 characters"),
            );
        }
    }

    if let Some(last_name) = fields.get("last_name") {
        if !last_name.is_empty() && last_name.len() > 50 {
            errors.insert(
                "last_name".into(),
                json!("Last name must be between 1 and 50 characters"),
            );
        }
    }

    if let Some(password) = fields.get("password") {
        if !password.is_empty() {
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
        }
    }

    if let Some(lang) = fields.get("preferred_language") {
        if !lang.is_empty() && lang != "en" && lang != "pt" {
            errors.insert(
                "preferred_language".into(),
                json!("Language must be 'en' or 'pt'"),
            );
        }
    }

    if !errors.is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": "validation_error", "fields": errors})),
        ));
    }

    // ── Hash new password if provided ────────────────────────────────────────
    let new_password_hash: Option<String> = if let Some(pw) = fields.get("password") {
        if pw.is_empty() {
            None
        } else {
            let pw = pw.clone();
            Some(
                tokio::task::spawn_blocking(move || bcrypt::hash(&pw, 12))
                    .await
                    .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "hashing_failed", ""))?
                    .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "hashing_failed", ""))?,
            )
        }
    } else {
        None
    };

    // ── Build COALESCE UPDATE (keep existing value for NULL params) ──────────
    let email = fields.get("email").filter(|s| !s.is_empty()).map(|s| s.as_str());
    let username = fields.get("username").filter(|s| !s.is_empty()).map(|s| s.as_str());
    let first_name = fields.get("first_name").filter(|s| !s.is_empty()).map(|s| s.as_str());
    let last_name = fields.get("last_name").filter(|s| !s.is_empty()).map(|s| s.as_str());
    let preferred_language = fields
        .get("preferred_language")
        .filter(|s| !s.is_empty())
        .map(|s| s.as_str());

    let updated = sqlx::query(
        r#"UPDATE users SET
            email               = COALESCE($1, email),
            username            = COALESCE($2, username),
            first_name          = COALESCE($3, first_name),
            last_name           = COALESCE($4, last_name),
            password_hash       = COALESCE($5, password_hash),
            profile_picture_url = COALESCE($6, profile_picture_url),
            preferred_language  = COALESCE($7, preferred_language),
            updated_at          = NOW()
           WHERE id = $8
           RETURNING id, username, first_name, last_name,
                     profile_picture_url, preferred_language"#,
    )
    .bind(email)
    .bind(username)
    .bind(first_name)
    .bind(last_name)
    .bind(new_password_hash.as_deref())
    .bind(new_picture_url.as_deref())
    .bind(preferred_language)
    .bind(id)
    .fetch_one(&state.db)
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
        api_err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "database_error",
            &e.to_string(),
        )
    })?;

    Ok((
        StatusCode::OK,
        Json(json!({
            "id":                  updated.try_get::<Uuid, _>("id").unwrap().to_string(),
            "username":            updated.try_get::<String, _>("username").unwrap(),
            "first_name":          updated.try_get::<String, _>("first_name").unwrap(),
            "last_name":           updated.try_get::<String, _>("last_name").unwrap(),
            "profile_picture_url": updated.try_get::<Option<String>, _>("profile_picture_url").unwrap(),
            "preferred_language":  updated.try_get::<String, _>("preferred_language").unwrap(),
        })),
    ))
}

// ── GET /api/users (legacy — authenticated check) ────────────────────────────
pub async fn list_users(
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let claims = jwt::verify_from_headers(&headers)?;
    Ok(Json(json!({
        "authenticated_as": {
            "user_id":  claims.user_id,
            "username": claims.username,
        }
    })))
}
