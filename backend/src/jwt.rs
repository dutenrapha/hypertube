use axum::{http::HeaderMap, http::StatusCode, Json};
use jsonwebtoken::{decode, encode, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Claims {
    pub user_id: String,
    pub username: String,
    pub exp: usize,
}

pub fn jwt_secret() -> String {
    std::env::var("JWT_SECRET")
        .unwrap_or_else(|_| "dev_jwt_secret_change_in_production".to_string())
}

pub fn create_token(user_id: Uuid, username: &str) -> Result<String, jsonwebtoken::errors::Error> {
    let exp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as usize
        + 24 * 3600;

    let claims = Claims {
        user_id: user_id.to_string(),
        username: username.to_string(),
        exp,
    };

    encode(
        &Header::default(),
        &claims,
        &EncodingKey::from_secret(jwt_secret().as_bytes()),
    )
}

/// Verify a JWT token string. Used e.g. for query-param auth on video streams.
pub fn verify_token(
    token: &str,
) -> Result<Claims, (StatusCode, Json<serde_json::Value>)> {
    decode::<Claims>(
        token,
        &DecodingKey::from_secret(jwt_secret().as_bytes()),
        &Validation::default(),
    )
    .map(|data| data.claims)
    .map_err(|_| {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid_or_expired_token"})),
        )
    })
}

/// Extract and verify a JWT from the `Authorization: Bearer <token>` header.
/// Returns the decoded Claims or a ready-to-return 401 response tuple.
pub fn verify_from_headers(
    headers: &HeaderMap,
) -> Result<Claims, (StatusCode, Json<serde_json::Value>)> {
    let auth_header = headers.get("Authorization");
    let token = auth_header
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer "))
        .ok_or_else(|| {
            eprintln!(
                "[auth] 401 UNAUTHORIZED: missing or bad Authorization header (present={}, value_len={})",
                auth_header.is_some(),
                auth_header
                    .and_then(|h| h.to_str().ok())
                    .map(|s| s.len())
                    .unwrap_or(0)
            );
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "missing_or_invalid_token"})),
            )
        })?;

    decode::<Claims>(
        token,
        &DecodingKey::from_secret(jwt_secret().as_bytes()),
        &Validation::default(),
    )
    .map(|data| data.claims)
    .map_err(|e| {
        eprintln!(
            "[auth] 401 UNAUTHORIZED: JWT decode failed — kind={:?} msg={} token_preview={}...",
            e.kind(),
            e,
            token.chars().take(20).collect::<String>()
        );
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "invalid_or_expired_token"})),
        )
    })
}
