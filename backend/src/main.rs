use axum::{
    extract::DefaultBodyLimit,
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use sqlx::postgres::PgPoolOptions;
use std::net::SocketAddr;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

mod jwt;
mod routes;

#[derive(Clone)]
pub struct AppState {
    pub db: sqlx::PgPool,
}

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

#[tokio::main]
async fn main() {
    let database_url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set");

    let pool = loop {
        match PgPoolOptions::new()
            .max_connections(5)
            .connect(&database_url)
            .await
        {
            Ok(p) => break p,
            Err(e) => {
                eprintln!("DB not ready yet ({e}), retrying in 2s…");
                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
            }
        }
    };

    sqlx::migrate!("./migrations")
        .run(&pool)
        .await
        .expect("Failed to run migrations");

    println!("Migrations applied successfully");

    let state = AppState { db: pool };

    let app = Router::new()
        .route("/health", get(health))
        // Serve uploaded profile pictures
        .nest_service("/uploads", ServeDir::new("/uploads"))
        // Auth
        .route("/api/auth/register", post(routes::auth::register))
        .route("/api/auth/login", post(routes::auth::login))
        // OAuth2 — 42 School
        .route("/api/auth/oauth/42", get(routes::oauth42::oauth42_redirect))
        .route("/api/auth/oauth/42/callback", get(routes::oauth42::oauth42_callback))
        // OAuth2 — Google
        .route("/api/auth/oauth/google", get(routes::oauth_google::oauth_google_redirect))
        .route("/api/auth/oauth/google/callback", get(routes::oauth_google::oauth_google_callback))
        // Password reset
        .route("/api/auth/forgot-password", post(routes::password_reset::forgot_password))
        .route("/api/auth/reset-password", post(routes::password_reset::reset_password))
        // User profiles
        .route("/api/users", get(routes::users::list_users))
        .route(
            "/api/users/:id",
            get(routes::users::get_user).patch(routes::users::update_user),
        )
        // Allow up to 10 MB globally; file size is enforced per-field in handlers
        .layer(DefaultBodyLimit::max(10 * 1024 * 1024))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 8000));
    println!("Backend listening on {addr}");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
