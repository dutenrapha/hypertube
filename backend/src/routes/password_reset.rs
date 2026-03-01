use axum::{extract::State, http::StatusCode, Json};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sqlx::Row;
use uuid::Uuid;

use crate::AppState;

type ApiError = (StatusCode, Json<Value>);
type ApiResult = Result<(StatusCode, Json<Value>), ApiError>;

fn api_err(status: StatusCode, code: &str, msg: &str) -> ApiError {
    (status, Json(json!({"error": code, "message": msg})))
}

fn sha256_hex(input: &str) -> String {
    hex::encode(Sha256::digest(input.as_bytes()))
}

#[derive(Deserialize)]
pub struct ForgotPasswordRequest {
    email: String,
}

#[derive(Deserialize)]
pub struct ResetPasswordRequest {
    token: String,
    new_password: String,
}

// ── POST /api/auth/forgot-password ────────────────────────────────────────
pub async fn forgot_password(
    State(state): State<AppState>,
    Json(body): Json<ForgotPasswordRequest>,
) -> (StatusCode, Json<Value>) {
    let ok = || {
        (
            StatusCode::OK,
            Json(json!({"message": "If your email is registered, you will receive a reset link shortly."})),
        )
    };

    let pool = &state.db;

    // Look up user — always return 200 to avoid email enumeration
    let user = match sqlx::query("SELECT id, email FROM users WHERE email = $1")
        .bind(&body.email)
        .fetch_optional(pool)
        .await
    {
        Ok(Some(row)) => row,
        _ => return ok(),
    };

    let user_id: Uuid = user.try_get("id").unwrap();
    let user_email: String = user.try_get("email").unwrap();

    // Generate a cryptographically secure 32-byte random token
    use rand::RngCore;
    let mut token_bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut token_bytes);
    let token_plain = hex::encode(token_bytes);
    let token_hash = sha256_hex(&token_plain);

    // Save SHA256 hash (expires in 1 hour)
    if sqlx::query(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) \
         VALUES ($1, $2, NOW() + INTERVAL '1 hour')",
    )
    .bind(user_id)
    .bind(&token_hash)
    .execute(pool)
    .await
    .is_err()
    {
        return ok();
    }

    // Build reset link
    let frontend_url = std::env::var("FRONTEND_URL")
        .unwrap_or_else(|_| "http://localhost:3000".to_string());
    let reset_link = format!("{}/reset-password?token={}", frontend_url, token_plain);

    // SMTP settings
    let smtp_host = std::env::var("SMTP_HOST").unwrap_or_else(|_| "localhost".to_string());
    let smtp_port: u16 = std::env::var("SMTP_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(1025);
    let smtp_from = std::env::var("SMTP_FROM")
        .unwrap_or_else(|_| "noreply@hypertube.local".to_string());

    // Send email in a blocking thread (lettre SMTP is synchronous)
    let send_result = tokio::task::spawn_blocking(move || {
        use lettre::{message::header::ContentType, Message, SmtpTransport, Transport};

        let from_mailbox = smtp_from
            .parse()
            .unwrap_or_else(|_| "noreply@hypertube.local".parse().unwrap());
        let to_mailbox = user_email
            .parse()
            .unwrap_or_else(|_| "user@example.com".parse().unwrap());

        let email = Message::builder()
            .from(from_mailbox)
            .to(to_mailbox)
            .subject("Reset your Hypertube password")
            .header(ContentType::TEXT_PLAIN)
            .body(format!(
                "Click the link below to reset your Hypertube password (valid for 1 hour):\n\n\
                 {}\n\n\
                 If you did not request a password reset, please ignore this email.",
                reset_link
            ))
            .map_err(|e| e.to_string())?;

        let mailer = SmtpTransport::builder_dangerous(&smtp_host)
            .port(smtp_port)
            .build();

        mailer.send(&email).map_err(|e| e.to_string())?;
        Ok::<(), String>(())
    })
    .await;

    if let Ok(Err(e)) = &send_result {
        eprintln!("Email send error: {e}");
    }

    ok()
}

// ── POST /api/auth/reset-password ─────────────────────────────────────────
pub async fn reset_password(
    State(state): State<AppState>,
    Json(body): Json<ResetPasswordRequest>,
) -> ApiResult {
    if body.token.is_empty() {
        return Err(api_err(
            StatusCode::BAD_REQUEST,
            "invalid_token",
            "Token is required",
        ));
    }

    let token_hash = sha256_hex(&body.token);
    let pool = &state.db;

    // Look up token by hash
    let token_row =
        sqlx::query("SELECT id, user_id, expires_at, used_at FROM password_reset_tokens WHERE token_hash = $1")
            .bind(&token_hash)
            .fetch_optional(pool)
            .await
            .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "db_error", ""))?
            .ok_or_else(|| api_err(StatusCode::BAD_REQUEST, "invalid_token", "Token not found or invalid"))?;

    // Check not already used
    let used_at: Option<chrono::DateTime<chrono::Utc>> =
        token_row.try_get("used_at").unwrap_or(None);
    if used_at.is_some() {
        return Err(api_err(
            StatusCode::BAD_REQUEST,
            "token_already_used",
            "This reset link has already been used",
        ));
    }

    // Check not expired
    let expires_at: chrono::DateTime<chrono::Utc> = token_row.try_get("expires_at").unwrap();
    if chrono::Utc::now() > expires_at {
        return Err(api_err(
            StatusCode::BAD_REQUEST,
            "token_expired",
            "This reset link has expired",
        ));
    }

    let user_id: Uuid = token_row.try_get("user_id").unwrap();
    let token_id: Uuid = token_row.try_get("id").unwrap();

    // Validate new password (same rules as registration)
    let pw = &body.new_password;
    if pw.len() < 8 {
        return Err(api_err(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Password must be at least 8 characters",
        ));
    }
    if !pw.chars().any(|c| c.is_uppercase()) {
        return Err(api_err(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Password must contain at least one uppercase letter",
        ));
    }
    if !pw.chars().any(|c| c.is_lowercase()) {
        return Err(api_err(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Password must contain at least one lowercase letter",
        ));
    }
    if !pw.chars().any(|c| c.is_ascii_digit()) {
        return Err(api_err(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Password must contain at least one number",
        ));
    }

    // bcrypt hash new password
    let password = body.new_password.clone();
    let password_hash = tokio::task::spawn_blocking(move || bcrypt::hash(&password, 12))
        .await
        .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "hashing_failed", ""))?
        .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "hashing_failed", ""))?;

    // Update user password
    sqlx::query("UPDATE users SET password_hash = $1, updated_at = NOW() WHERE id = $2")
        .bind(&password_hash)
        .bind(user_id)
        .execute(pool)
        .await
        .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "db_error", ""))?;

    // Mark token as used
    sqlx::query("UPDATE password_reset_tokens SET used_at = NOW() WHERE id = $1")
        .bind(token_id)
        .execute(pool)
        .await
        .map_err(|_| api_err(StatusCode::INTERNAL_SERVER_ERROR, "db_error", ""))?;

    Ok((
        StatusCode::OK,
        Json(json!({"message": "Password reset successfully"})),
    ))
}
