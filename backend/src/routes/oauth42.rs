use std::collections::HashMap;

use axum::{
    body::Body,
    extract::{Query, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde_json::{json, Value};
use sqlx::Row;
use uuid::Uuid;

use crate::{jwt, AppState};

fn api_base_42() -> String {
    std::env::var("OAUTH_42_API_BASE_URL")
        .unwrap_or_else(|_| "https://api.intra.42.fr".to_string())
}

fn found(location: &str) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, location)
        .body(Body::empty())
        .unwrap()
}

// ── GET /api/auth/oauth/42 ─────────────────────────────────────────────────
/// Redirect the browser to 42's authorization page.
pub async fn oauth42_redirect() -> impl IntoResponse {
    let client_id = std::env::var("OAUTH_42_CLIENT_ID").unwrap_or_default();
    let redirect_uri = std::env::var("OAUTH_42_REDIRECT_URI").unwrap_or_default();
    let encoded_redirect = urlencoding::encode(&redirect_uri);
    let url = format!(
        "https://api.intra.42.fr/oauth/authorize\
         ?client_id={client_id}&redirect_uri={encoded_redirect}&response_type=code"
    );
    found(&url)
}

// ── GET /api/auth/oauth/42/callback?code=XXX ──────────────────────────────
/// Handle the OAuth2 callback from 42.
/// Exchanges the code for an access_token, fetches user info,
/// creates or links the user in the DB, then redirects to the frontend with a JWT.
pub async fn oauth42_callback(
    State(state): State<AppState>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Response, (StatusCode, Json<Value>)> {
    let code = params
        .get("code")
        .ok_or_else(|| (StatusCode::BAD_REQUEST, Json(json!({"error": "missing_code"}))))?
        .to_owned();

    let client_id = std::env::var("OAUTH_42_CLIENT_ID").unwrap_or_default();
    let client_secret = std::env::var("OAUTH_42_CLIENT_SECRET").unwrap_or_default();
    let redirect_uri = std::env::var("OAUTH_42_REDIRECT_URI").unwrap_or_default();
    let api_base = api_base_42();

    let http = reqwest::Client::new();

    // ── 1. Exchange code for access_token ──────────────────────────────────
    let token_resp = http
        .post(format!("{api_base}/oauth/token"))
        .form(&[
            ("grant_type", "authorization_code"),
            ("client_id", client_id.as_str()),
            ("client_secret", client_secret.as_str()),
            ("code", code.as_str()),
            ("redirect_uri", redirect_uri.as_str()),
        ])
        .send()
        .await
        .map_err(|e| {
            eprintln!("42 token exchange error: {e}");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "token_exchange_failed"})),
            )
        })?;

    if !token_resp.status().is_success() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid_or_expired_code"})),
        ));
    }

    let token_json: Value = token_resp.json().await.map_err(|_| {
        (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": "token_parse_failed"})),
        )
    })?;

    let access_token = token_json["access_token"]
        .as_str()
        .ok_or_else(|| {
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "no_access_token"})),
            )
        })?
        .to_owned();

    // ── 2. Fetch user info from 42 API ─────────────────────────────────────
    let user_resp = http
        .get(format!("{api_base}/v2/me"))
        .bearer_auth(&access_token)
        .send()
        .await
        .map_err(|_| {
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "userinfo_failed"})),
            )
        })?;

    if !user_resp.status().is_success() {
        return Err((
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": "userinfo_error"})),
        ));
    }

    let info: Value = user_resp.json().await.map_err(|_| {
        (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": "userinfo_parse"})),
        )
    })?;

    let provider_user_id = match &info["id"] {
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        _ => {
            return Err((
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "no_provider_id"})),
            ))
        }
    };

    let email = info["email"]
        .as_str()
        .ok_or_else(|| (StatusCode::BAD_GATEWAY, Json(json!({"error": "no_email"}))))?
        .to_owned();

    let login = info["login"].as_str().unwrap_or("user42").to_owned();
    let first_name = info["first_name"].as_str().unwrap_or("User").to_owned();
    let last_name = info["last_name"].as_str().unwrap_or("42").to_owned();
    let pic_url: Option<String> = info["image"]["link"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_owned);

    let pool = &state.db;

    // ── 3. Find or create user by email ────────────────────────────────────
    let existing = sqlx::query("SELECT id, username FROM users WHERE email = $1")
        .bind(&email)
        .fetch_optional(pool)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?;

    let (user_id, username): (Uuid, String) = if let Some(row) = existing {
        (
            row.try_get("id").unwrap(),
            row.try_get("username").unwrap(),
        )
    } else {
        let new_id = Uuid::new_v4();

        // Build a valid username from the 42 login (alphanum + underscore, 3-20 chars)
        let base: String = login
            .chars()
            .filter(|c| c.is_alphanumeric() || *c == '_')
            .take(20)
            .collect();
        let base = if base.len() < 3 {
            format!("u42_{}", &new_id.to_string()[..8])
        } else {
            base
        };

        // Append a short suffix if username is already taken
        let uname = {
            let taken = sqlx::query("SELECT id FROM users WHERE username = $1")
                .bind(&base)
                .fetch_optional(pool)
                .await
                .map_err(|_| {
                    (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({"error": "db_error"})),
                    )
                })?;
            if taken.is_none() {
                base.clone()
            } else {
                let suffix = &new_id.to_string().replace('-', "")[..4];
                format!("{}_{suffix}", &base[..base.len().min(15)])
            }
        };

        sqlx::query(
            "INSERT INTO users (id, email, username, first_name, last_name, profile_picture_url) \
             VALUES ($1, $2, $3, $4, $5, $6)",
        )
        .bind(new_id)
        .bind(&email)
        .bind(&uname)
        .bind(&first_name)
        .bind(&last_name)
        .bind(&pic_url)
        .execute(pool)
        .await
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "create_user_failed"})),
            )
        })?;

        (new_id, uname)
    };

    // ── 4. Link oauth_account (idempotent) ─────────────────────────────────
    sqlx::query(
        "INSERT INTO oauth_accounts (user_id, provider, provider_user_id) \
         VALUES ($1, '42', $2) \
         ON CONFLICT (provider, provider_user_id) DO NOTHING",
    )
    .bind(user_id)
    .bind(&provider_user_id)
    .execute(pool)
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "oauth_link_failed"})),
        )
    })?;

    // ── 5. Issue JWT and redirect to frontend ──────────────────────────────
    let token = jwt::create_token(user_id, &username).map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "jwt_failed"})),
        )
    })?;

    let frontend_url = std::env::var("FRONTEND_URL").unwrap_or_else(|_| "http://localhost:5173".to_string());
    let redirect_to = format!("{frontend_url}/auth/callback?token={token}");
    Ok(found(&redirect_to))
}
