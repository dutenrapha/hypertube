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

fn found(location: &str) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, location)
        .body(Body::empty())
        .unwrap()
}

fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
        .unwrap_or_else(|_| reqwest::Client::new())
}

// ── GET /api/auth/oauth/google ─────────────────────────────────────────────
/// Redirect the browser to Google's OAuth2 authorization page.
pub async fn oauth_google_redirect() -> impl IntoResponse {
    let client_id = std::env::var("OAUTH_GOOGLE_CLIENT_ID").unwrap_or_default();
    let redirect_uri = std::env::var("OAUTH_GOOGLE_REDIRECT_URI").unwrap_or_default();
    let encoded_redirect = urlencoding::encode(&redirect_uri);
    let scope = "email%20profile";
    let url = format!(
        "https://accounts.google.com/o/oauth2/v2/auth\
         ?client_id={client_id}&redirect_uri={encoded_redirect}\
         &response_type=code&scope={scope}"
    );
    found(&url)
}

// ── GET /api/auth/oauth/google/callback?code=XXX ──────────────────────────
/// Handle the Google OAuth2 callback.
/// Exchanges the code for an access_token, fetches user info from Google,
/// creates or links the user in the DB, and redirects to the frontend with a JWT.
pub async fn oauth_google_callback(
    State(state): State<AppState>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Response, (StatusCode, Json<Value>)> {
    let code = params
        .get("code")
        .ok_or_else(|| (StatusCode::BAD_REQUEST, Json(json!({"error": "missing_code"}))))?
        .to_owned();

    let client_id = std::env::var("OAUTH_GOOGLE_CLIENT_ID").unwrap_or_default();
    let client_secret = std::env::var("OAUTH_GOOGLE_CLIENT_SECRET").unwrap_or_default();
    let redirect_uri = std::env::var("OAUTH_GOOGLE_REDIRECT_URI").unwrap_or_default();

    let http = http_client();

    // ── 1. Exchange code for access_token ──────────────────────────────────
    let token_resp = http
        .post("https://oauth2.googleapis.com/token")
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
            eprintln!("Google token exchange error: {e}");
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

    // ── 2. Fetch user info from Google ─────────────────────────────────────
    let user_resp = http
        .get("https://www.googleapis.com/oauth2/v2/userinfo")
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

    let provider_user_id = info["id"]
        .as_str()
        .ok_or_else(|| {
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": "no_provider_id"})),
            )
        })?
        .to_owned();

    let email = info["email"]
        .as_str()
        .ok_or_else(|| (StatusCode::BAD_GATEWAY, Json(json!({"error": "no_email"}))))?
        .to_owned();

    let first_name = info["given_name"].as_str().unwrap_or("User").to_owned();
    let last_name = info["family_name"].as_str().unwrap_or("Google").to_owned();
    let pic_url: Option<String> = info["picture"]
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

        // Build username from email prefix (before @)
        let base: String = email
            .split('@')
            .next()
            .unwrap_or("user")
            .chars()
            .filter(|c| c.is_alphanumeric() || *c == '_')
            .take(20)
            .collect();
        let base = if base.len() < 3 {
            format!("g_{}", &new_id.to_string()[..8])
        } else {
            base
        };

        // Append suffix if username is already taken
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
         VALUES ($1, 'google', $2) \
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

    let frontend_url = std::env::var("FRONTEND_URL")
        .unwrap_or_else(|_| "http://localhost:3000".to_string());
    Ok(found(&format!("{frontend_url}/auth/callback?token={token}")))
}
