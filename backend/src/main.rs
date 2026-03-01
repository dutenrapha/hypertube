use axum::{Router, routing::get, Json};
use serde_json::{json, Value};
use std::net::SocketAddr;
use tower_http::cors::CorsLayer;

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

#[tokio::main]
async fn main() {
    let app = Router::new()
        .route("/health", get(health))
        .layer(CorsLayer::permissive());

    let addr = SocketAddr::from(([0, 0, 0, 0], 8000));
    println!("Backend listening on {addr}");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
