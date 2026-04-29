use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::Response,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncSeekExt};
use tokio_util::io::ReaderStream;
use uuid::Uuid;

use crate::routes::movies;
use crate::{jwt, AppState};

type ApiError = (StatusCode, Json<Value>);

#[derive(Deserialize)]
pub struct StreamBody {
    pub magnet: String,
}

#[derive(Deserialize, Default)]
pub struct StreamArchiveQuery {
    pub token: Option<String>,
}

fn aria2_url() -> String {
    std::env::var("ARIA2_RPC_URL")
        .unwrap_or_else(|_| "http://aria2c:6800/jsonrpc".to_string())
}

fn aria2_secret() -> String {
    std::env::var("ARIA2_RPC_SECRET").unwrap_or_default()
}

fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap_or_default()
}

/// Client for proxying external video streams (long timeout, follows redirects).
fn proxy_http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(3600))
        .redirect(reqwest::redirect::Policy::default())
        .user_agent("Mozilla/5.0 (compatible; Hypertube/1.0)")
        .build()
        .unwrap_or_default()
}

fn is_native_format(path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.ends_with(".mp4") || lower.ends_with(".webm")
}

fn mp4_output_path(input_path: &str) -> String {
    if let Some(dot_pos) = input_path.rfind('.') {
        format!("{}.mp4", &input_path[..dot_pos])
    } else {
        format!("{}.mp4", input_path)
    }
}

/// Atomically claim conversion: sets is_converting = TRUE only if it was FALSE.
/// Returns true if we successfully claimed the conversion slot.
async fn try_claim_conversion(db: &sqlx::PgPool, movie_id: Uuid) -> bool {
    sqlx::query(
        "UPDATE movies SET is_converting = TRUE \
         WHERE id = $1 AND is_converting = FALSE",
    )
    .bind(movie_id)
    .execute(db)
    .await
    .map(|r| r.rows_affected() > 0)
    .unwrap_or(false)
}

/// Spawn FFmpeg conversion in the background.
/// On success: update file_path to .mp4 and clear is_converting.
/// On failure: clear is_converting.
fn trigger_ffmpeg_conversion(db: sqlx::PgPool, movie_id: Uuid, input_path: String) {
    tokio::spawn(async move {
        let output_path = mp4_output_path(&input_path);
        let result = tokio::process::Command::new("ffmpeg")
            .args([
                "-i",
                &input_path,
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-movflags",
                "faststart",
                "-y",
                &output_path,
            ])
            .status()
            .await;

        match result {
            Ok(s) if s.success() => {
                let _ = sqlx::query(
                    "UPDATE movies SET file_path = $1, is_converting = FALSE, downloaded_at = NOW() \
                     WHERE id = $2",
                )
                .bind(&output_path)
                .bind(movie_id)
                .execute(&db)
                .await;
            }
            _ => {
                let _ = sqlx::query(
                    "UPDATE movies SET is_converting = FALSE WHERE id = $1",
                )
                .bind(movie_id)
                .execute(&db)
                .await;
            }
        }
    });
}

async fn aria2_add_uri(magnet: &str) -> Option<String> {
    let token = format!("token:{}", aria2_secret());
    let body = json!({
        "jsonrpc": "2.0",
        "method": "aria2.addUri",
        "params": [
            token,
            [magnet],
            {
                "dir": "/downloads",
                "bt-prioritize-piece": "head,tail",
                "seed-time": "0"
            }
        ],
        "id": "1"
    });
    let resp = http_client().post(aria2_url()).json(&body).send().await.ok()?;
    let json: Value = resp.json().await.ok()?;
    json["result"].as_str().map(|s| s.to_string())
}

async fn aria2_tell_status(gid: &str) -> Option<Value> {
    let token = format!("token:{}", aria2_secret());
    let body = json!({
        "jsonrpc": "2.0",
        "method": "aria2.tellStatus",
        "params": [
            token,
            gid,
            ["status", "completedLength", "totalLength", "files", "dir"]
        ],
        "id": "1"
    });
    let resp = http_client().post(aria2_url()).json(&body).send().await.ok()?;
    let json: Value = resp.json().await.ok()?;
    let result = json["result"].clone();
    if result.is_object() {
        Some(result)
    } else {
        None
    }
}

fn find_video_file(files: &Value) -> Option<String> {
    let files = files.as_array()?;
    let video_exts = ["mp4", "avi", "mkv", "ogv", "webm"];
    let mut best: Option<(String, u64)> = None;
    for f in files {
        let path = f["path"].as_str().unwrap_or("");
        if path.is_empty() {
            continue;
        }
        let lower = path.to_lowercase();
        let ext = lower.rsplit('.').next().unwrap_or("");
        if video_exts.contains(&ext) {
            let size: u64 = f["length"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            if best.is_none() || size > best.as_ref().unwrap().1 {
                best = Some((path.to_string(), size));
            }
        }
    }
    best.map(|(p, _)| p)
}

fn parse_range(range_str: &str, file_size: u64) -> Option<(u64, u64)> {
    let range = range_str.strip_prefix("bytes=")?;
    let (start_str, end_str) = range.split_once('-')?;
    let start: u64 = start_str.parse().ok()?;
    let end: u64 = if end_str.is_empty() {
        file_size.saturating_sub(1)
    } else {
        let e: u64 = end_str.parse().ok()?;
        e.min(file_size.saturating_sub(1))
    };
    if start > end {
        return None;
    }
    Some((start, end))
}

async fn upsert_watched(
    db: &sqlx::PgPool,
    user_id: Uuid,
    movie_id: Uuid,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO watched_movies (user_id, movie_id, watched_at) \
         VALUES ($1, $2, NOW()) \
         ON CONFLICT (user_id, movie_id) DO UPDATE SET watched_at = NOW()",
    )
    .bind(user_id)
    .bind(movie_id)
    .execute(db)
    .await?;

    // Update movies.last_watched_at so the cleanup job can track staleness
    sqlx::query("UPDATE movies SET last_watched_at = NOW() WHERE id = $1")
        .bind(movie_id)
        .execute(db)
        .await?;

    Ok(())
}

/// Look up (or create) a movies row for the given external_id (= imdb_id field
/// in our schema), then mark it as watched for `user_id`. Used by the archive.org
/// proxy stream where the row may not have been created yet via /api/movies/:id.
async fn upsert_watched_by_imdb_id(
    db: &sqlx::PgPool,
    user_id: Uuid,
    imdb_id: &str,
) -> Result<(), sqlx::Error> {
    let row = sqlx::query(
        "INSERT INTO movies (title, imdb_id) VALUES ($1, $2) \
         ON CONFLICT (imdb_id) DO UPDATE SET imdb_id = EXCLUDED.imdb_id \
         RETURNING id",
    )
    .bind(imdb_id)
    .bind(imdb_id)
    .fetch_one(db)
    .await?;
    let movie_id: Uuid = row.try_get("id")?;
    upsert_watched(db, user_id, movie_id).await
}

/// Returns true when this HTTP request looks like the *start* of a playback
/// session (no Range header, or Range starts at byte 0). Avoids writing to the
/// DB on every subsequent Range chunk requested by the <video> element.
fn is_initial_playback_request(headers: &HeaderMap) -> bool {
    match headers
        .get(header::RANGE)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("bytes="))
    {
        None => true,
        Some(spec) => spec
            .split_once('-')
            .map(|(start, _)| start.trim() == "0")
            .unwrap_or(false),
    }
}

// ── POST /api/movies/:external_id/stream ──────────────────────────────────────

pub async fn start_stream(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    State(state): State<AppState>,
    Json(body): Json<StreamBody>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let claims = jwt::verify_from_headers(&headers)?;
    let user_id = claims
        .user_id
        .parse::<Uuid>()
        .map_err(|_| (StatusCode::UNAUTHORIZED, Json(json!({"error": "invalid_user"}))))?;

    let row = sqlx::query(
        "SELECT id, file_path, aria2_gid, is_converting FROM movies WHERE imdb_id = $1",
    )
    .bind(&external_id)
    .fetch_optional(&state.db)
    .await
    .unwrap_or(None)
    .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "movie_not_found"}))))?;

    let movie_id: Uuid = row.try_get("id").unwrap();
    let file_path: Option<String> = row.try_get("file_path").unwrap_or(None);
    let aria2_gid: Option<String> = row.try_get("aria2_gid").unwrap_or(None);
    let is_converting: bool = row.try_get("is_converting").unwrap_or(false);

    let magnet = body.magnet.trim().to_string();

    // Persist magnet if not already stored
    if !magnet.is_empty() {
        let _ = sqlx::query(
            "UPDATE movies SET torrent_magnet = COALESCE(torrent_magnet, $1) WHERE id = $2",
        )
        .bind(&magnet)
        .bind(movie_id)
        .execute(&state.db)
        .await;
    }

    // Conversion in progress
    if is_converting {
        let _ = upsert_watched(&state.db, user_id, movie_id).await;
        return Ok((
            StatusCode::ACCEPTED,
            Json(json!({"status": "converting", "gid": aria2_gid})),
        ));
    }

    // File already downloaded
    if let Some(ref fp) = file_path {
        if tokio::fs::metadata(fp).await.is_ok() {
            if is_native_format(fp) {
                let _ = upsert_watched(&state.db, user_id, movie_id).await;
                return Ok((
                    StatusCode::ACCEPTED,
                    Json(json!({"status": "ready", "gid": aria2_gid})),
                ));
            } else {
                // Non-native: check for converted mp4
                let mp4_path = mp4_output_path(fp);
                if tokio::fs::metadata(&mp4_path).await.is_ok() {
                    let _ = sqlx::query("UPDATE movies SET file_path = $1 WHERE id = $2")
                        .bind(&mp4_path)
                        .bind(movie_id)
                        .execute(&state.db)
                        .await;
                    let _ = upsert_watched(&state.db, user_id, movie_id).await;
                    return Ok((
                        StatusCode::ACCEPTED,
                        Json(json!({"status": "ready", "gid": aria2_gid})),
                    ));
                } else {
                    if try_claim_conversion(&state.db, movie_id).await {
                        trigger_ffmpeg_conversion(state.db.clone(), movie_id, fp.clone());
                    }
                    let _ = upsert_watched(&state.db, user_id, movie_id).await;
                    return Ok((
                        StatusCode::ACCEPTED,
                        Json(json!({"status": "converting", "gid": aria2_gid})),
                    ));
                }
            }
        }
    }

    // Download already in progress in aria2
    if let Some(ref gid) = aria2_gid {
        if aria2_tell_status(gid).await.is_some() {
            let _ = upsert_watched(&state.db, user_id, movie_id).await;
            return Ok((
                StatusCode::ACCEPTED,
                Json(json!({"status": "downloading", "gid": gid})),
            ));
        }
    }

    if magnet.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "magnet_required"})),
        ));
    }

    let gid = aria2_add_uri(&magnet).await.ok_or_else(|| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error": "aria2_unavailable"})),
        )
    })?;

    let _ = sqlx::query("UPDATE movies SET aria2_gid = $1 WHERE id = $2")
        .bind(&gid)
        .bind(movie_id)
        .execute(&state.db)
        .await;

    let _ = upsert_watched(&state.db, user_id, movie_id).await;

    Ok((
        StatusCode::ACCEPTED,
        Json(json!({"status": "downloading", "gid": gid})),
    ))
}

// ── GET /api/movies/:external_id/status ───────────────────────────────────────

pub async fn stream_status(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    jwt::verify_from_headers(&headers)?;

    let row = sqlx::query(
        "SELECT id, file_path, aria2_gid, is_converting FROM movies WHERE imdb_id = $1",
    )
    .bind(&external_id)
    .fetch_optional(&state.db)
    .await
    .unwrap_or(None)
    .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "movie_not_found"}))))?;

    let movie_id: Uuid = row.try_get("id").unwrap();
    let file_path: Option<String> = row.try_get("file_path").unwrap_or(None);
    let aria2_gid: Option<String> = row.try_get("aria2_gid").unwrap_or(None);
    let is_converting: bool = row.try_get("is_converting").unwrap_or(false);

    // Conversion in progress
    if is_converting {
        return Ok(Json(json!({
            "status": "converting",
            "progress": 100,
            "file_path": null
        })));
    }

    // File already on disk
    if let Some(ref fp) = file_path {
        if tokio::fs::metadata(fp).await.is_ok() {
            if is_native_format(fp) {
                return Ok(Json(json!({
                    "status": "ready",
                    "progress": 100,
                    "file_path": fp
                })));
            } else {
                // Non-native: check for converted mp4
                let mp4_path = mp4_output_path(fp);
                if tokio::fs::metadata(&mp4_path).await.is_ok() {
                    let _ = sqlx::query("UPDATE movies SET file_path = $1 WHERE id = $2")
                        .bind(&mp4_path)
                        .bind(movie_id)
                        .execute(&state.db)
                        .await;
                    return Ok(Json(json!({
                        "status": "ready",
                        "progress": 100,
                        "file_path": mp4_path
                    })));
                } else {
                    if try_claim_conversion(&state.db, movie_id).await {
                        trigger_ffmpeg_conversion(state.db.clone(), movie_id, fp.clone());
                    }
                    return Ok(Json(json!({
                        "status": "converting",
                        "progress": 100,
                        "file_path": null
                    })));
                }
            }
        }
    }

    // Query aria2
    if let Some(ref gid) = aria2_gid {
        if let Some(status_val) = aria2_tell_status(gid).await {
            let aria2_status = status_val["status"].as_str().unwrap_or("unknown");
            let completed: u64 = status_val["completedLength"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            let total: u64 = status_val["totalLength"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            let progress: u8 = if total > 0 {
                ((completed * 100) / total).min(100) as u8
            } else {
                0
            };

            let video_path = find_video_file(&status_val["files"]);

            // Early-ready only for native formats; non-native must be fully downloaded
            let is_ready = if let Some(ref vp) = video_path {
                if is_native_format(vp) {
                    aria2_status == "complete" || progress > 5 || completed > 10 * 1024 * 1024
                } else {
                    aria2_status == "complete"
                }
            } else {
                false
            };

            if is_ready {
                if let Some(ref vp) = video_path {
                    let _ = sqlx::query(
                        "UPDATE movies SET file_path = $1, downloaded_at = NOW() WHERE id = $2",
                    )
                    .bind(vp)
                    .bind(movie_id)
                    .execute(&state.db)
                    .await;

                    // Non-native complete: trigger conversion
                    if aria2_status == "complete" && !is_native_format(vp) {
                        if try_claim_conversion(&state.db, movie_id).await {
                            trigger_ffmpeg_conversion(state.db.clone(), movie_id, vp.clone());
                        }
                        return Ok(Json(json!({
                            "status": "converting",
                            "progress": 100,
                            "file_path": null
                        })));
                    }
                }
                return Ok(Json(json!({
                    "status": "ready",
                    "progress": if aria2_status == "complete" { 100u8 } else { progress },
                    "file_path": video_path
                })));
            }

            return Ok(Json(json!({
                "status": "downloading",
                "progress": progress,
                "file_path": null
            })));
        }
    }

    Ok(Json(json!({
        "status": "not_started",
        "progress": 0,
        "file_path": null
    })))
}

// ── GET /api/movies/:external_id/stream ───────────────────────────────────────

pub async fn serve_stream(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    State(state): State<AppState>,
) -> Result<Response<Body>, ApiError> {
    jwt::verify_from_headers(&headers)?;

    let row = sqlx::query("SELECT file_path FROM movies WHERE imdb_id = $1")
        .bind(&external_id)
        .fetch_optional(&state.db)
        .await
        .unwrap_or(None)
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "movie_not_found"}))))?;

    let file_path: Option<String> = row.try_get("file_path").unwrap_or(None);
    let file_path = file_path
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "file_not_ready"}))))?;

    // For non-native formats, try the converted mp4 output
    let file_path = if !is_native_format(&file_path) {
        let mp4_path = mp4_output_path(&file_path);
        if tokio::fs::metadata(&mp4_path).await.is_ok() {
            mp4_path
        } else {
            return Err((
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"error": "converting"})),
            ));
        }
    } else {
        file_path
    };

    let metadata = tokio::fs::metadata(&file_path)
        .await
        .map_err(|_| (StatusCode::NOT_FOUND, Json(json!({"error": "file_not_accessible"}))))?;

    let file_size = metadata.len();

    let content_type = {
        let lower = file_path.to_lowercase();
        if lower.ends_with(".mp4") {
            "video/mp4"
        } else if lower.ends_with(".mkv") {
            "video/x-matroska"
        } else if lower.ends_with(".avi") {
            "video/x-msvideo"
        } else if lower.ends_with(".webm") {
            "video/webm"
        } else {
            "video/mp4"
        }
    };

    let range_header = headers
        .get(header::RANGE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    if let Some(range_str) = range_header {
        let (start, end) = parse_range(&range_str, file_size).ok_or_else(|| {
            (
                StatusCode::RANGE_NOT_SATISFIABLE,
                Json(json!({"error": "invalid_range"})),
            )
        })?;
        let length = end - start + 1;

        let mut file = tokio::fs::File::open(&file_path).await.map_err(|_| {
            (StatusCode::NOT_FOUND, Json(json!({"error": "file_open_error"})))
        })?;

        file.seek(std::io::SeekFrom::Start(start))
            .await
            .map_err(|_| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "seek_error"})),
                )
            })?;

        let mut buf = vec![0u8; length as usize];
        let mut offset = 0usize;
        while offset < buf.len() {
            match file.read(&mut buf[offset..]).await {
                Ok(0) => break,
                Ok(n) => offset += n,
                Err(_) => break,
            }
        }
        buf.truncate(offset);
        let actual_len = buf.len();

        Ok(Response::builder()
            .status(StatusCode::PARTIAL_CONTENT)
            .header(header::CONTENT_TYPE, content_type)
            .header(header::ACCEPT_RANGES, "bytes")
            .header(
                header::CONTENT_RANGE,
                format!("bytes {}-{}/{}", start, end, file_size),
            )
            .header(header::CONTENT_LENGTH, actual_len.to_string())
            .body(Body::from(buf))
            .unwrap())
    } else {
        let file = tokio::fs::File::open(&file_path).await.map_err(|_| {
            (StatusCode::NOT_FOUND, Json(json!({"error": "file_open_error"})))
        })?;
        let stream = ReaderStream::new(file);

        Ok(Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, content_type)
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CONTENT_LENGTH, file_size.to_string())
            .body(Body::from_stream(stream))
            .unwrap())
    }
}

// ── GET /api/movies/:external_id/stream/archive ───────────────────────────────
// Proxies archive.org video to avoid CORS; supports Range for seeking.

pub async fn serve_archive_stream(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    Query(query): Query<StreamArchiveQuery>,
    State(state): State<AppState>,
) -> Result<Response<Body>, ApiError> {
    let token = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .or_else(|| query.token.as_deref());
    let claims = token
        .ok_or_else(|| {
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "missing_or_invalid_token"})),
            )
        })
        .and_then(|t| jwt::verify_token(t))
        .map_err(|e| e)?;

    // Mark as watched on the *first* request of a playback session. The HTML
    // <video> element issues many Range requests; we only persist on the
    // initial one (no Range, or Range bytes=0-...) to avoid hammering the DB.
    if is_initial_playback_request(&headers) {
        if let Ok(user_uuid) = claims.user_id.parse::<Uuid>() {
            let _ = upsert_watched_by_imdb_id(&state.db, user_uuid, &external_id).await;
        }
    }

    let video_url = movies::fetch_archive_video_url(&external_id)
        .await
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "archive_video_not_found"}))))?;

    let client = proxy_http_client();
    let mut req_builder = client.get(&video_url);
    if let Some(r) = headers.get(header::RANGE) {
        if let Ok(v) = r.to_str() {
            req_builder = req_builder.header(header::RANGE, v);
        }
    }
    let resp = req_builder
        .send()
        .await
        .map_err(|_| (StatusCode::BAD_GATEWAY, Json(json!({"error": "upstream_failed"}))))?;

    let status = resp.status();
    let mut builder = Response::builder().status(status);

    if let Some(v) = resp.headers().get(header::CONTENT_TYPE) {
        let _ = builder = builder.header(header::CONTENT_TYPE, v.clone());
    }
    if let Some(v) = resp.headers().get(header::CONTENT_RANGE) {
        let _ = builder = builder.header(header::CONTENT_RANGE, v.clone());
    }
    if let Some(v) = resp.headers().get(header::CONTENT_LENGTH) {
        let _ = builder = builder.header(header::CONTENT_LENGTH, v.clone());
    }
    if let Some(v) = resp.headers().get(header::ACCEPT_RANGES) {
        let _ = builder = builder.header(header::ACCEPT_RANGES, v.clone());
    }

    let body = Body::from_stream(resp.bytes_stream());
    builder.body(body).map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "body_error"})),
        )
    })
}
