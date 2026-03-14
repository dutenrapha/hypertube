use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;
use std::time::Duration;
use uuid::Uuid;

use crate::{jwt, AppState};

type ApiError = (StatusCode, Json<Value>);

const OPENSUBTITLES_API_BASE: &str = "https://api.opensubtitles.com/api/v1";
const SUBTITLE_DIR: &str = "/downloads/subtitles";

fn os_api_key() -> String {
    std::env::var("OPENSUBTITLES_API_KEY").unwrap_or_default()
}

fn os_user_agent() -> String {
    std::env::var("OPENSUBTITLES_USER_AGENT")
        .unwrap_or_else(|_| "Hypertube v1.0".to_string())
}

fn http_client() -> reqwest::Client {
    let ua = os_user_agent();
    reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .user_agent(ua)
        .build()
        .unwrap_or_default()
}

/// Search OpenSubtitles for a subtitle. Returns the file_id of the first match.
async fn search_subtitle(title: &str, lang: &str) -> Option<u64> {
    let api_key = os_api_key();
    if api_key.is_empty() {
        return None;
    }

    let url = format!(
        "{}/subtitles?query={}&languages={}&type=movie",
        OPENSUBTITLES_API_BASE,
        urlencoding::encode(title),
        lang
    );

    let resp = http_client()
        .get(&url)
        .header("Api-Key", &api_key)
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        eprintln!("[subtitles] OpenSubtitles search failed: {}", resp.status());
        return None;
    }

    let json: Value = resp.json().await.ok()?;
    let data = json["data"].as_array()?;

    for item in data {
        let item_lang = item["attributes"]["language"].as_str().unwrap_or("");
        if item_lang == lang {
            if let Some(files) = item["attributes"]["files"].as_array() {
                if let Some(file_id) = files.first().and_then(|f| f["file_id"].as_u64()) {
                    return Some(file_id);
                }
            }
        }
    }
    None
}

/// Get the download link for a subtitle file_id.
async fn get_download_link(file_id: u64) -> Option<String> {
    let api_key = os_api_key();
    if api_key.is_empty() {
        return None;
    }

    let url = format!("{}/download", OPENSUBTITLES_API_BASE);

    let resp = http_client()
        .post(&url)
        .header("Api-Key", &api_key)
        .header("Content-Type", "application/json")
        .json(&json!({"file_id": file_id, "sub_format": "srt"}))
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        eprintln!("[subtitles] OpenSubtitles download request failed: {}", resp.status());
        return None;
    }

    let json: Value = resp.json().await.ok()?;
    json["link"].as_str().map(|s| s.to_string())
}

/// Convert SRT subtitle content to WebVTT format.
/// Only replaces commas in timestamp lines (lines containing " --> ").
fn srt_to_vtt(srt: &str) -> String {
    let mut result = String::from("WEBVTT\n\n");
    for line in srt.lines() {
        if line.contains(" --> ") {
            result.push_str(&line.replace(',', "."));
        } else {
            result.push_str(line);
        }
        result.push('\n');
    }
    result
}

/// Download subtitle content, convert to WebVTT, and save to disk.
async fn download_subtitle_file(link: &str, dest_path: &str) -> bool {
    let resp = match reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap_or_default()
        .get(link)
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[subtitles] Failed to download subtitle: {}", e);
            return false;
        }
    };

    let bytes = match resp.bytes().await {
        Ok(b) => b,
        Err(_) => return false,
    };

    if let Some(parent) = std::path::Path::new(dest_path).parent() {
        let _ = tokio::fs::create_dir_all(parent).await;
    }

    let srt_text = String::from_utf8_lossy(&bytes);
    let vtt_content = srt_to_vtt(&srt_text);
    tokio::fs::write(dest_path, vtt_content.as_bytes()).await.is_ok()
}

/// Return the serve URL for a subtitle if it exists on disk or can be fetched.
async fn fetch_subtitle_for_lang(
    movie_uuid: Uuid,
    title: &str,
    lang: &str,
) -> Option<String> {
    let file_path = format!("{}/{}/{}.vtt", SUBTITLE_DIR, movie_uuid, lang);
    let serve_url = format!("/subtitles/{}/{}.vtt", movie_uuid, lang);

    // Already cached on disk
    if tokio::fs::metadata(&file_path).await.is_ok() {
        return Some(serve_url);
    }

    // Try OpenSubtitles
    let file_id = search_subtitle(title, lang).await?;
    let link = get_download_link(file_id).await?;

    if download_subtitle_file(&link, &file_path).await {
        eprintln!("[subtitles] Saved {} subtitle for '{}' → {}", lang, title, file_path);
        Some(serve_url)
    } else {
        None
    }
}

// Minimal valid WebVTT so the CC button always appears even when no subtitles are found
const EMPTY_VTT: &[u8] = b"WEBVTT\n\n1\n00:00:00.000 --> 00:00:00.001\n \n\n";

pub async fn empty_subtitle() -> impl IntoResponse {
    (
        StatusCode::OK,
        [(
            axum::http::header::CONTENT_TYPE,
            "text/vtt; charset=utf-8",
        )],
        EMPTY_VTT,
    )
        .into_response()
}

// ── GET /api/subtitles/proxy?url=... (for external e.g. archive.org — avoids CORS)
#[derive(Deserialize)]
pub struct ProxyQuery {
    url: String,
}

pub async fn proxy_subtitle(Query(q): Query<ProxyQuery>) -> impl IntoResponse {
    let url = q.url.trim();
    if !url.starts_with("https://archive.org/") {
        return (
            StatusCode::FORBIDDEN,
            [(axum::http::header::CONTENT_TYPE, "text/plain")],
            "Only archive.org URLs are allowed",
        )
            .into_response();
    }
    let resp = match reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap_or_default()
        .get(url)
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[subtitles] proxy fetch failed: {}", e);
            return (
                StatusCode::BAD_GATEWAY,
                [(axum::http::header::CONTENT_TYPE, "text/plain")],
                "Failed to fetch subtitle",
            )
                .into_response();
        }
    };
    if !resp.status().is_success() {
        return (
            StatusCode::BAD_GATEWAY,
            [(axum::http::header::CONTENT_TYPE, "text/plain")],
            "Upstream returned error",
        )
            .into_response();
    }
    let bytes = match resp.bytes().await {
        Ok(b) => b,
        Err(_) => {
            return (
                StatusCode::BAD_GATEWAY,
                [(axum::http::header::CONTENT_TYPE, "text/plain")],
                "Failed to read body",
            )
                .into_response();
        }
    };
    let srt_text = String::from_utf8_lossy(&bytes);
    let vtt_content = srt_to_vtt(&srt_text);
    (
        StatusCode::OK,
        [(
            axum::http::header::CONTENT_TYPE,
            "text/vtt; charset=utf-8",
        )],
        vtt_content.into_bytes(),
    )
        .into_response()
}

// ── GET /api/movies/:external_id/subtitles ────────────────────────────────────

pub async fn get_subtitles(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    let claims = jwt::verify_from_headers(&headers)?;

    let user_id = claims
        .user_id
        .parse::<Uuid>()
        .map_err(|_| (StatusCode::UNAUTHORIZED, Json(json!({"error": "invalid_user"}))))?;

    // Get movie info
    let row = sqlx::query("SELECT id, title FROM movies WHERE imdb_id = $1")
        .bind(&external_id)
        .fetch_optional(&state.db)
        .await
        .unwrap_or(None)
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "movie_not_found"}))))?;

    let movie_uuid: Uuid = row.try_get("id").unwrap();
    let title: String = row.try_get("title").unwrap_or_default();

    // Get user's preferred language
    let preferred_lang: String = sqlx::query_scalar(
        "SELECT preferred_language FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(&state.db)
    .await
    .unwrap_or(None)
    .unwrap_or_else(|| "en".to_string());

    // Always include English; add preferred language if different
    let mut langs: Vec<String> = vec!["en".to_string()];
    if preferred_lang != "en" {
        langs.push(preferred_lang);
    }

    let mut result: Vec<Value> = Vec::new();
    for lang in &langs {
        if let Some(url) = fetch_subtitle_for_lang(movie_uuid, &title, lang).await {
            result.push(json!({ "lang": lang, "url": url }));
        }
    }

    Ok(Json(json!(result)))
}
