use axum::{
    body::Body,
    extract::DefaultBodyLimit,
    http::Request,
    middleware::{self, Next},
    response::Response,
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

/// Log every incoming request: method, path, and whether Authorization header is present.
async fn request_logger(req: Request<Body>, next: Next) -> Response {
    let method = req.method().as_str().to_string();
    let path = req.uri().path().to_string();
    let query = req.uri().query().unwrap_or("").to_string();
    let full = if query.is_empty() {
        path.clone()
    } else {
        format!("{}?{}", path, query)
    };
    let has_auth = req.headers().get("Authorization").is_some();
    eprintln!(
        "[http] --> {} {} auth_header={}",
        method,
        full,
        if has_auth { "yes" } else { "NO" }
    );
    let response = next.run(req).await;
    let status = response.status().as_u16();
    eprintln!("[http] <-- {} status={}", method, status);
    response
}

#[derive(Clone)]
pub struct AppState {
    pub db: sqlx::PgPool,
    pub redis: redis::aio::ConnectionManager,
}

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

#[tokio::main]
async fn main() {
    let database_url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set");
    let redis_url =
        std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://redis:6379".to_string());

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

    let redis_client =
        redis::Client::open(redis_url).expect("Failed to open Redis client");
    let redis_manager = redis::aio::ConnectionManager::new(redis_client)
        .await
        .expect("Failed to connect to Redis");

    sqlx::migrate!("./migrations")
        .run(&pool)
        .await
        .expect("Failed to run migrations");

    println!("Migrations applied successfully");

    let state = AppState {
        db: pool,
        redis: redis_manager,
    };

    let app = Router::new()
        .route("/health", get(health))
        // Serve uploaded profile pictures
        .nest_service("/uploads", ServeDir::new("/uploads"))
        // Auth
        .route("/api/auth/register", post(routes::auth::register))
        .route("/api/auth/login", post(routes::auth::login))
        // OAuth2 — 42 School
        .route("/api/auth/oauth/42", get(routes::oauth42::oauth42_redirect))
        .route(
            "/api/auth/oauth/42/callback",
            get(routes::oauth42::oauth42_callback),
        )
        // OAuth2 — Google
        .route(
            "/api/auth/oauth/google",
            get(routes::oauth_google::oauth_google_redirect),
        )
        .route(
            "/api/auth/oauth/google/callback",
            get(routes::oauth_google::oauth_google_callback),
        )
        // Password reset
        .route(
            "/api/auth/forgot-password",
            post(routes::password_reset::forgot_password),
        )
        .route(
            "/api/auth/reset-password",
            post(routes::password_reset::reset_password),
        )
        // User profiles
        .route("/api/users", get(routes::users::list_users))
        .route(
            "/api/users/:id",
            get(routes::users::get_user).patch(routes::users::update_user),
        )
        // Search
        .route("/api/search", get(routes::search::search))
        // Movie details + comments
        .route("/api/movies/:id", get(routes::movies::get_movie))
        .route(
            "/api/movies/:id/comments",
            get(routes::movies::list_comments).post(routes::movies::create_comment),
        )
        // Streaming via aria2
        .route(
            "/api/movies/:id/stream",
            post(routes::stream::start_stream).get(routes::stream::serve_stream),
        )
        .route(
            "/api/movies/:id/stream/archive",
            get(routes::stream::serve_archive_stream),
        )
        .route("/api/movies/:id/status", get(routes::stream::stream_status))
        // Allow up to 10 MB globally; file size is enforced per-field in handlers
        .layer(DefaultBodyLimit::max(10 * 1024 * 1024))
        .layer(middleware::from_fn(request_logger))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 8000));
    println!("Backend listening on {addr}");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
