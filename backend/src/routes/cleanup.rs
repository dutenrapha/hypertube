use axum::{extract::State, http::StatusCode, Json};
use serde_json::{json, Value};
use sqlx::Row;

use crate::AppState;

/// Delete video files that have not been watched for more than 30 days.
/// Silently ignores files that no longer exist on disk.
pub async fn run_cleanup(db: &sqlx::PgPool) {
    let rows = sqlx::query(
        "SELECT id, file_path FROM movies \
         WHERE last_watched_at < NOW() - INTERVAL '30 days' \
         AND file_path IS NOT NULL",
    )
    .fetch_all(db)
    .await;

    match rows {
        Ok(rows) => {
            for row in rows {
                let id: uuid::Uuid = match row.try_get("id") {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                let file_path: String = match row.try_get("file_path") {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                // Delete file; silently ignore if it no longer exists
                match std::fs::remove_file(&file_path) {
                    Ok(_) => eprintln!("[cleanup] Deleted file: {}", file_path),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                        eprintln!("[cleanup] File not found (skipping): {}", file_path);
                    }
                    Err(e) => eprintln!("[cleanup] Failed to delete {}: {}", file_path, e),
                }

                // Clear file_path in DB regardless of whether deletion succeeded
                let _ = sqlx::query("UPDATE movies SET file_path = NULL WHERE id = $1")
                    .bind(id)
                    .execute(db)
                    .await;
            }
        }
        Err(e) => eprintln!("[cleanup] Query error: {}", e),
    }
}

/// POST /api/admin/cleanup — trigger cleanup immediately (used in tests and admin ops).
pub async fn trigger_cleanup(
    State(state): State<AppState>,
) -> (StatusCode, Json<Value>) {
    run_cleanup(&state.db).await;
    (StatusCode::OK, Json(json!({"status": "ok"})))
}
