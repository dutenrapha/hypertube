use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    Json,
};
use hex;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sqlx::Row;
use uuid::Uuid;

use crate::AppState;

type ApiError = (StatusCode, Json<Value>);

fn sha256_hex(input: &str) -> String {
    hex::encode(Sha256::digest(input.as_bytes()))
}

fn random_token() -> String {
    format!(
        "{}{}",
        Uuid::new_v4().simple().to_string(),
        Uuid::new_v4().simple().to_string()
    )
}

/// Verify the Bearer OAuth2 token. Returns (client_uuid, Option<user_uuid>).
async fn verify_oauth_token(
    headers: &HeaderMap,
    pool: &sqlx::PgPool,
) -> Result<(Uuid, Option<Uuid>), ApiError> {
    let auth = headers
        .get("Authorization")
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| {
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "missing_token"})),
            )
        })?;

    let token = auth.strip_prefix("Bearer ").ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid_token_format"})),
        )
    })?;

    let token_hash = sha256_hex(token);

    let row = sqlx::query(
        "SELECT t.client_id, t.user_id \
         FROM oauth2_tokens t \
         WHERE t.token_hash = $1 AND t.expires_at > NOW()",
    )
    .bind(&token_hash)
    .fetch_optional(pool)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?
    .ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid_or_expired_token"})),
        )
    })?;

    let client_uuid: Uuid = row.try_get("client_id").unwrap();
    let user_id: Option<Uuid> = row.try_get("user_id").unwrap_or(None);

    Ok((client_uuid, user_id))
}

// ── POST /oauth/token ─────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TokenRequest {
    client_id: String,
    client_secret: String,
    grant_type: String,
    // For password grant
    username: Option<String>,
    password: Option<String>,
}

pub async fn oauth_token(
    State(state): State<AppState>,
    Json(body): Json<TokenRequest>,
) -> Result<Json<Value>, ApiError> {
    if body.grant_type != "client_credentials" && body.grant_type != "password" {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "unsupported_grant_type"})),
        ));
    }

    // Validate client credentials
    let client_row = sqlx::query(
        "SELECT id, client_secret_hash FROM oauth2_clients WHERE client_id = $1",
    )
    .bind(&body.client_id)
    .fetch_optional(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?
    .ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid_client"})),
        )
    })?;

    let client_uuid: Uuid = client_row.try_get("id").unwrap();
    let stored_hash: String = client_row.try_get("client_secret_hash").unwrap();
    let provided_hash = sha256_hex(&body.client_secret);

    if stored_hash != provided_hash {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid_client"})),
        ));
    }

    // For password grant, validate user credentials
    let user_id: Option<Uuid> = if body.grant_type == "password" {
        let username = body.username.as_deref().ok_or_else(|| {
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "username_required"})),
            )
        })?;
        let password = body.password.as_deref().ok_or_else(|| {
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "password_required"})),
            )
        })?;

        let user_row = sqlx::query(
            "SELECT id, password_hash FROM users WHERE username = $1 OR email = $1",
        )
        .bind(username)
        .fetch_optional(&state.db)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?
        .ok_or_else(|| {
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "invalid_credentials"})),
            )
        })?;

        let user_uuid: Uuid = user_row.try_get("id").unwrap();
        let password_hash: Option<String> = user_row.try_get("password_hash").unwrap_or(None);

        let hash = password_hash.ok_or_else(|| {
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "invalid_credentials"})),
            )
        })?;

        let pwd = password.to_string();
        let valid = tokio::task::spawn_blocking(move || {
            bcrypt::verify(&pwd, &hash).unwrap_or(false)
        })
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal_error"})),
            )
        })?;

        if !valid {
            return Err((
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "invalid_credentials"})),
            ));
        }

        Some(user_uuid)
    } else {
        None
    };

    // Generate and store the token
    let token = random_token();
    let token_hash = sha256_hex(&token);
    let expires_at = chrono::Utc::now() + chrono::Duration::seconds(3600);

    sqlx::query(
        "INSERT INTO oauth2_tokens (client_id, user_id, token_hash, expires_at) \
         VALUES ($1, $2, $3, $4)",
    )
    .bind(client_uuid)
    .bind(user_id)
    .bind(&token_hash)
    .bind(expires_at)
    .execute(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?;

    Ok(Json(json!({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 3600
    })))
}

// ── GET /users ────────────────────────────────────────────────────────────────

pub async fn list_users_oauth(
    headers: HeaderMap,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    verify_oauth_token(&headers, &state.db).await?;

    let rows = sqlx::query("SELECT id, username FROM users ORDER BY created_at DESC LIMIT 100")
        .fetch_all(&state.db)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?;

    let users: Vec<Value> = rows
        .iter()
        .map(|r| {
            let id: Uuid = r.try_get("id").unwrap();
            let username: String = r.try_get("username").unwrap();
            json!({"id": id, "username": username})
        })
        .collect();

    Ok(Json(json!(users)))
}

// ── GET /users/:id ────────────────────────────────────────────────────────────

pub async fn get_user_oauth(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    verify_oauth_token(&headers, &state.db).await?;

    let row = sqlx::query(
        "SELECT username, email, profile_picture_url FROM users WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?
    .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "not_found"}))))?;

    let username: String = row.try_get("username").unwrap();
    let email: String = row.try_get("email").unwrap();
    let pic: Option<String> = row.try_get("profile_picture_url").unwrap_or(None);

    Ok(Json(json!({
        "username": username,
        "email": email,
        "profile_picture_url": pic
    })))
}

// ── PATCH /users/:id ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct UpdateUserRequest {
    username: Option<String>,
    email: Option<String>,
    password: Option<String>,
    profile_picture_url: Option<String>,
}

pub async fn update_user_oauth(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
    Json(body): Json<UpdateUserRequest>,
) -> Result<Json<Value>, ApiError> {
    let (_, user_id) = verify_oauth_token(&headers, &state.db).await?;

    let uid = user_id.ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "user_context_required"})),
        )
    })?;

    if uid != id {
        return Err((
            StatusCode::FORBIDDEN,
            Json(json!({"error": "forbidden"})),
        ));
    }

    // Build update
    let new_hash: Option<String> = if let Some(pwd) = &body.password {
        let p = pwd.clone();
        let h = tokio::task::spawn_blocking(move || {
            bcrypt::hash(&p, 12).ok()
        })
        .await
        .unwrap_or(None);
        h
    } else {
        None
    };

    sqlx::query(
        "UPDATE users SET \
          username = COALESCE($1, username), \
          email = COALESCE($2, email), \
          password_hash = COALESCE($3, password_hash), \
          profile_picture_url = COALESCE($4, profile_picture_url), \
          updated_at = NOW() \
         WHERE id = $5",
    )
    .bind(body.username.as_deref())
    .bind(body.email.as_deref())
    .bind(new_hash.as_deref())
    .bind(body.profile_picture_url.as_deref())
    .bind(id)
    .execute(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?;

    Ok(Json(json!({"status": "updated"})))
}

// ── GET /movies ───────────────────────────────────────────────────────────────

pub async fn list_movies_oauth(
    headers: HeaderMap,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    verify_oauth_token(&headers, &state.db).await?;

    let rows = sqlx::query("SELECT id, title FROM movies ORDER BY created_at DESC LIMIT 100")
        .fetch_all(&state.db)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?;

    let movies: Vec<Value> = rows
        .iter()
        .map(|r| {
            let id: Uuid = r.try_get("id").unwrap();
            let title: String = r.try_get("title").unwrap();
            json!({"id": id, "name": title})
        })
        .collect();

    Ok(Json(json!(movies)))
}

// ── GET /movies/:id ───────────────────────────────────────────────────────────

pub async fn get_movie_oauth(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    verify_oauth_token(&headers, &state.db).await?;

    let row = sqlx::query(
        "SELECT id, title, imdb_rating::float8 AS imdb_rating, year, length_minutes, \
         (SELECT COUNT(*) FROM comments WHERE movie_id = m.id) AS comments_count \
         FROM movies m WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?
    .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "not_found"}))))?;

    let mid: Uuid = row.try_get("id").unwrap();
    let title: String = row.try_get("title").unwrap();
    let imdb_rating: Option<f64> = row.try_get("imdb_rating").unwrap_or(None);
    let year: Option<i32> = row.try_get("year").unwrap_or(None);
    let length_minutes: Option<i32> = row.try_get("length_minutes").unwrap_or(None);
    let comments_count: i64 = row.try_get("comments_count").unwrap_or(0);

    let rating_str = imdb_rating.map(|r| format!("{:.1}", r));

    Ok(Json(json!({
        "id": mid,
        "name": title,
        "imdb_rating": rating_str,
        "year": year,
        "length": length_minutes,
        "available_subtitles": [],
        "comments_count": comments_count
    })))
}

// ── GET /comments ─────────────────────────────────────────────────────────────

pub async fn list_comments_oauth(
    headers: HeaderMap,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    verify_oauth_token(&headers, &state.db).await?;

    let rows = sqlx::query(
        "SELECT c.id, c.content, c.created_at, u.username \
         FROM comments c \
         JOIN users u ON c.user_id = u.id \
         ORDER BY c.created_at DESC LIMIT 50",
    )
    .fetch_all(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?;

    let comments: Vec<Value> = rows
        .iter()
        .map(|r| {
            let id: Uuid = r.try_get("id").unwrap();
            let content: String = r.try_get("content").unwrap();
            let created_at: chrono::DateTime<chrono::Utc> = r.try_get("created_at").unwrap();
            let username: String = r.try_get("username").unwrap();
            json!({
                "id": id,
                "content": content,
                "author_username": username,
                "date": created_at.to_rfc3339()
            })
        })
        .collect();

    Ok(Json(json!(comments)))
}

// ── GET /comments/:id ─────────────────────────────────────────────────────────

pub async fn get_comment_oauth(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    verify_oauth_token(&headers, &state.db).await?;

    let row = sqlx::query(
        "SELECT c.id, c.content, c.created_at, u.username \
         FROM comments c \
         JOIN users u ON c.user_id = u.id \
         WHERE c.id = $1",
    )
    .bind(id)
    .fetch_optional(&state.db)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?
    .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "not_found"}))))?;

    let comment_id: Uuid = row.try_get("id").unwrap();
    let content: String = row.try_get("content").unwrap();
    let created_at: chrono::DateTime<chrono::Utc> = row.try_get("created_at").unwrap();
    let username: String = row.try_get("username").unwrap();

    Ok(Json(json!({
        "comment_id": comment_id,
        "comment": content,
        "author_username": username,
        "date_posted": created_at.to_rfc3339()
    })))
}

// ── PATCH /comments/:id ───────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct UpdateCommentRequest {
    comment: Option<String>,
}

pub async fn update_comment_oauth(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
    Json(body): Json<UpdateCommentRequest>,
) -> Result<Json<Value>, ApiError> {
    let (_, user_id) = verify_oauth_token(&headers, &state.db).await?;

    let uid = user_id.ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "user_context_required"})),
        )
    })?;

    // Check comment ownership
    let owner_row = sqlx::query("SELECT user_id FROM comments WHERE id = $1")
        .bind(id)
        .fetch_optional(&state.db)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "not_found"}))))?;

    let owner_id: Uuid = owner_row.try_get("user_id").unwrap();
    if owner_id != uid {
        return Err((StatusCode::FORBIDDEN, Json(json!({"error": "forbidden"}))));
    }

    if let Some(new_content) = &body.comment {
        sqlx::query("UPDATE comments SET content = $1, updated_at = NOW() WHERE id = $2")
            .bind(new_content)
            .bind(id)
            .execute(&state.db)
            .await
            .map_err(|_| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "db_error"})),
                )
            })?;
    }

    Ok(Json(json!({"status": "updated"})))
}

// ── DELETE /comments/:id ──────────────────────────────────────────────────────

pub async fn delete_comment_oauth(
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    let (_, user_id) = verify_oauth_token(&headers, &state.db).await?;

    let uid = user_id.ok_or_else(|| {
        (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "user_context_required"})),
        )
    })?;

    // Check comment ownership
    let owner_row = sqlx::query("SELECT user_id FROM comments WHERE id = $1")
        .bind(id)
        .fetch_optional(&state.db)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "not_found"}))))?;

    let owner_id: Uuid = owner_row.try_get("user_id").unwrap();
    if owner_id != uid {
        return Err((StatusCode::FORBIDDEN, Json(json!({"error": "forbidden"}))));
    }

    sqlx::query("DELETE FROM comments WHERE id = $1")
        .bind(id)
        .execute(&state.db)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?;

    Ok(Json(json!({"status": "deleted"})))
}

// ── POST /comments ────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CreateCommentRequest {
    movie_id: Uuid,
    content: String,
}

pub async fn create_comment_oauth(
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(body): Json<CreateCommentRequest>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let (_, user_id) = verify_oauth_token(&headers, &state.db).await?;

    let uid = user_id.ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "user_context_required"})),
        )
    })?;

    if body.content.trim().is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": "content_required"})),
        ));
    }

    let row = sqlx::query(
        "INSERT INTO comments (user_id, movie_id, content) VALUES ($1, $2, $3) RETURNING id",
    )
    .bind(uid)
    .bind(body.movie_id)
    .bind(body.content.trim())
    .fetch_one(&state.db)
    .await
    .map_err(|e| {
        eprintln!("[oauth2] create_comment error: {}", e);
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": "invalid_movie_or_db_error"})),
        )
    })?;

    let comment_id: Uuid = row.try_get("id").unwrap();

    Ok((StatusCode::CREATED, Json(json!({"id": comment_id, "status": "created"}))))
}

// ── POST /movies/:movie_id/comments ──────────────────────────────────────────

#[derive(Deserialize)]
pub struct CreateMovieCommentRequest {
    content: String,
}

pub async fn create_movie_comment_oauth(
    headers: HeaderMap,
    Path(movie_id): Path<Uuid>,
    State(state): State<AppState>,
    Json(body): Json<CreateMovieCommentRequest>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let (_, user_id) = verify_oauth_token(&headers, &state.db).await?;

    let uid = user_id.ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "user_context_required"})),
        )
    })?;

    if body.content.trim().is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": "content_required"})),
        ));
    }

    let row = sqlx::query(
        "INSERT INTO comments (user_id, movie_id, content) VALUES ($1, $2, $3) RETURNING id",
    )
    .bind(uid)
    .bind(movie_id)
    .bind(body.content.trim())
    .fetch_one(&state.db)
    .await
    .map_err(|e| {
        eprintln!("[oauth2] create_movie_comment error: {}", e);
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"error": "invalid_movie_or_db_error"})),
        )
    })?;

    let comment_id: Uuid = row.try_get("id").unwrap();

    Ok((StatusCode::CREATED, Json(json!({"id": comment_id, "status": "created"}))))
}
