use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    Json,
};
use redis::AsyncCommands;
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;
use std::time::Duration;
use uuid::Uuid;

use crate::{jwt, AppState};

const MOVIE_CACHE_TTL: u64 = 3600; // 1 hour
const HTTP_TIMEOUT: u64 = 10;

type ApiError = (StatusCode, Json<Value>);

#[derive(Deserialize)]
pub struct MovieParams {
    title:     Option<String>,
    year:      Option<String>,
    cover_url: Option<String>,
}

#[derive(Deserialize)]
pub struct CommentBody {
    content: String,
}

fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(HTTP_TIMEOUT))
        .user_agent("Mozilla/5.0 (compatible; Hypertube/1.0)")
        .build()
        .unwrap_or_default()
}

async fn redis_get(conn: &mut redis::aio::ConnectionManager, key: &str) -> Option<String> {
    conn.get(key).await.ok()
}

async fn redis_set(
    conn: &mut redis::aio::ConnectionManager,
    key: &str,
    value: &str,
    ttl: u64,
) {
    let _: Result<(), _> = conn.set_ex(key, value, ttl).await;
}

/// Fetch subtitle (.srt/.vtt) URLs from archive.org for a given identifier.
/// PDT items don't have a metadata API, so we skip them.
async fn fetch_archive_subtitles(identifier: &str) -> Vec<String> {
    if identifier.starts_with("pdt-") {
        return vec![];
    }
    let client = http_client();
    let url = format!("https://archive.org/metadata/{}/files", identifier);
    let json: Value = match client.get(&url).send().await {
        Ok(r) => r.json().await.unwrap_or(json!({})),
        Err(_) => return vec![],
    };
    let files = match json["result"].as_array() {
        Some(f) => f.clone(),
        None => return vec![],
    };
    files
        .iter()
        .filter_map(|f| {
            let name = f["name"].as_str()?;
            if name.ends_with(".srt") || name.ends_with(".vtt") {
                Some(format!(
                    "https://archive.org/download/{}/{}",
                    identifier,
                    urlencoding::encode(name)
                ))
            } else {
                None
            }
        })
        .collect()
}

/// Fetch the best video file URL from archive.org for a given identifier.
/// Public so the stream proxy can use it.
pub async fn fetch_archive_video_url(identifier: &str) -> Option<String> {
    if identifier.starts_with("pdt-") {
        return None;
    }
    let client = http_client();
    let url = format!("https://archive.org/metadata/{}/files", identifier);
    let json: Value = match client.get(&url).send().await {
        Ok(r) => r.json().await.unwrap_or(json!({})),
        Err(_) => return None,
    };
    let files = json["result"].as_array()?.clone();
    // Prefer .mp4, fall back to .avi / .mkv
    let extensions = ["mp4", "avi", "mkv", "ogv"];
    for ext in extensions {
        for file in &files {
            let name = file["name"].as_str().unwrap_or("");
            if name.to_lowercase().ends_with(&format!(".{}", ext)) {
                return Some(format!(
                    "https://archive.org/download/{}/{}",
                    identifier,
                    urlencoding::encode(name)
                ));
            }
        }
    }
    None
}

/// Fetch full OMDb metadata for a title. Returns the raw OMDb JSON.
async fn fetch_omdb_full(title: &str) -> Value {
    let api_key = std::env::var("OMDB_API_KEY").unwrap_or_default();
    if api_key.is_empty() || title.is_empty() {
        return json!({});
    }
    let url = format!(
        "https://www.omdbapi.com/?t={}&apikey={}",
        urlencoding::encode(title),
        api_key
    );
    let client = http_client();
    match client.get(&url).send().await {
        Ok(r) => r.json::<Value>().await.unwrap_or(json!({})),
        Err(_) => json!({}),
    }
}

fn omdb_str(omdb: &Value, key: &str) -> Option<String> {
    omdb[key]
        .as_str()
        .filter(|s| !s.is_empty() && *s != "N/A")
        .map(|s| s.to_string())
}

fn split_cast(cast_str: Option<&str>) -> Vec<String> {
    cast_str
        .unwrap_or("")
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

// ── GET /api/movies/:external_id ──────────────────────────────────────────────

pub async fn get_movie(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    Query(params): Query<MovieParams>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    jwt::verify_from_headers(&headers)?;

    let mut conn = state.redis.clone();
    let cache_key = format!("movie:{}", external_id);

    // 1. Try Redis cache (non-user-specific)
    if let Some(cached) = redis_get(&mut conn, &cache_key).await {
        if let Ok(mut val) = serde_json::from_str::<Value>(&cached) {
            // Always fetch fresh comments_count
            if let Some(movie_id) = get_movie_uuid(&state.db, &external_id).await {
                let count: i64 = sqlx::query_scalar(
                    "SELECT COUNT(*) FROM comments WHERE movie_id = $1",
                )
                .bind(movie_id)
                .fetch_one(&state.db)
                .await
                .unwrap_or(0);
                val["comments_count"] = json!(count);
            }
            return Ok(Json(val));
        }
    }

    // 2. Try DB
    let db_row = sqlx::query(
        "SELECT id, title, year, imdb_rating::float8 AS imdb_rating, genre, \
         summary, director, cast_list, cover_url, length_minutes, torrent_magnet \
         FROM movies WHERE imdb_id = $1",
    )
    .bind(&external_id)
    .fetch_optional(&state.db)
    .await
    .unwrap_or(None);

    let (movie_id, movie_data) = if let Some(row) = db_row {
        let mid: Uuid = row.try_get("id").unwrap();
        let title: String = row.try_get("title").unwrap_or_default();
        let has_details = row
            .try_get::<Option<String>, _>("summary")
            .unwrap_or(None)
            .is_some();

        let (summary, director, cast_list, imdb_rating_str, genre, length_minutes) =
            if has_details {
                (
                    row.try_get::<Option<String>, _>("summary").unwrap_or(None),
                    row.try_get::<Option<String>, _>("director").unwrap_or(None),
                    row.try_get::<Option<String>, _>("cast_list").unwrap_or(None),
                    row.try_get::<Option<f64>, _>("imdb_rating")
                        .unwrap_or(None)
                        .map(|r| format!("{:.1}", r)),
                    row.try_get::<Option<String>, _>("genre").unwrap_or(None),
                    row.try_get::<Option<i32>, _>("length_minutes").unwrap_or(None),
                )
            } else {
                // Enrich from OMDb
                let lookup_title = params.title.as_deref().unwrap_or(&title);
                let omdb = fetch_omdb_full(lookup_title).await;
                let summary = omdb_str(&omdb, "Plot");
                let director = omdb_str(&omdb, "Director");
                let cast = omdb_str(&omdb, "Actors");
                let rating = omdb_str(&omdb, "imdbRating");
                let genre = omdb_str(&omdb, "Genre");
                let length_minutes: Option<i32> = omdb["Runtime"]
                    .as_str()
                    .and_then(|r| r.split_whitespace().next())
                    .and_then(|n| n.parse().ok());

                // Update DB
                let _ = sqlx::query(
                    "UPDATE movies SET \
                       summary = COALESCE($1, summary), \
                       director = COALESCE($2, director), \
                       cast_list = COALESCE($3, cast_list), \
                       imdb_rating = COALESCE($4::float8::numeric, imdb_rating), \
                       genre = COALESCE($5, genre), \
                       length_minutes = COALESCE($6, length_minutes) \
                     WHERE id = $7",
                )
                .bind(&summary)
                .bind(&director)
                .bind(&cast)
                .bind(rating.as_deref().and_then(|r| r.parse::<f64>().ok()))
                .bind(&genre)
                .bind(length_minutes)
                .bind(mid)
                .execute(&state.db)
                .await;

                (summary, director, cast, rating, genre, length_minutes)
            };

        let cover_url: Option<String> = row.try_get("cover_url").unwrap_or(None);
        let year: Option<i32> = row.try_get("year").unwrap_or(None);
        let torrent_magnet: Option<String> = row.try_get("torrent_magnet").unwrap_or(None);
        let cast_vec = split_cast(cast_list.as_deref());

        let subtitles = fetch_archive_subtitles(&external_id).await;
        let video_url = fetch_archive_video_url(&external_id).await;

        let data = json!({
            "id":                  external_id,
            "title":               title,
            "year":                year,
            "imdb_rating":         imdb_rating_str,
            "genre":               genre,
            "summary":             summary,
            "director":            director,
            "cast":                cast_vec,
            "cover_url":           cover_url,
            "length_minutes":      length_minutes,
            "available_subtitles": subtitles,
            "video_url":           video_url,
            "torrent_magnet":      torrent_magnet,
        });

        (mid, data)
    } else {
        // 3. Movie not in DB — create it from query params + OMDb
        let title = params
            .title
            .clone()
            .unwrap_or_else(|| external_id.replace('_', " "));
        let year: Option<i32> = params.year.as_deref().and_then(|y| y.parse().ok());
        let cover_url = params.cover_url.clone().or_else(|| {
            if !external_id.starts_with("pdt-") {
                Some(format!("https://archive.org/services/img/{}", external_id))
            } else {
                None
            }
        });

        let omdb = fetch_omdb_full(&title).await;
        let summary = omdb_str(&omdb, "Plot");
        let director = omdb_str(&omdb, "Director");
        let cast = omdb_str(&omdb, "Actors");
        let imdb_rating: Option<f64> = omdb_str(&omdb, "imdbRating")
            .as_deref()
            .and_then(|r| r.parse().ok());
        let genre = omdb_str(&omdb, "Genre");
        let length_minutes: Option<i32> = omdb["Runtime"]
            .as_str()
            .and_then(|r| r.split_whitespace().next())
            .and_then(|n| n.parse().ok());

        let row = sqlx::query(
            "INSERT INTO movies \
               (title, year, imdb_id, imdb_rating, genre, summary, director, cast_list, cover_url, length_minutes) \
             VALUES ($1, $2, $3, $4::float8::numeric, $5, $6, $7, $8, $9, $10) \
             ON CONFLICT (imdb_id) DO UPDATE SET \
               summary      = COALESCE(EXCLUDED.summary,        movies.summary), \
               director     = COALESCE(EXCLUDED.director,       movies.director), \
               cast_list    = COALESCE(EXCLUDED.cast_list,      movies.cast_list), \
               imdb_rating  = COALESCE(EXCLUDED.imdb_rating,    movies.imdb_rating), \
               genre        = COALESCE(EXCLUDED.genre,          movies.genre), \
               length_minutes = COALESCE(EXCLUDED.length_minutes, movies.length_minutes) \
             RETURNING id",
        )
        .bind(&title)
        .bind(year)
        .bind(&external_id)
        .bind(imdb_rating)
        .bind(&genre)
        .bind(&summary)
        .bind(&director)
        .bind(&cast)
        .bind(&cover_url)
        .bind(length_minutes)
        .fetch_one(&state.db)
        .await
        .map_err(|e| {
            eprintln!("DB error upserting movie: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db_error"})),
            )
        })?;

        let mid: Uuid = row.try_get("id").unwrap();
        let cast_vec = split_cast(cast.as_deref());
        let imdb_rating_str = imdb_rating.map(|r| format!("{:.1}", r));

        let subtitles = fetch_archive_subtitles(&external_id).await;
        let video_url = fetch_archive_video_url(&external_id).await;

        let data = json!({
            "id":                  external_id,
            "title":               title,
            "year":                year,
            "imdb_rating":         imdb_rating_str,
            "genre":               genre,
            "summary":             summary,
            "director":            director,
            "cast":                cast_vec,
            "cover_url":           cover_url,
            "length_minutes":      length_minutes,
            "available_subtitles": subtitles,
            "video_url":           video_url,
            "torrent_magnet":      Value::Null,
        });

        (mid, data)
    };

    // Cache movie data (without comments_count)
    if let Ok(s) = serde_json::to_string(&movie_data) {
        redis_set(&mut conn, &cache_key, &s, MOVIE_CACHE_TTL).await;
    }

    // Add fresh comments_count
    let comments_count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM comments WHERE movie_id = $1",
    )
    .bind(movie_id)
    .fetch_one(&state.db)
    .await
    .unwrap_or(0);

    let mut result = movie_data;
    result["comments_count"] = json!(comments_count);

    Ok(Json(result))
}

// ── GET /api/movies/:external_id/comments ─────────────────────────────────────

pub async fn list_comments(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    jwt::verify_from_headers(&headers)?;

    let movie_id = get_movie_uuid(&state.db, &external_id).await;
    let movie_id = match movie_id {
        Some(id) => id,
        None => return Ok(Json(json!({ "comments": [] }))),
    };

    let rows = sqlx::query(
        "SELECT c.id, c.content, c.created_at, c.user_id, u.username, u.profile_picture_url \
         FROM comments c JOIN users u ON u.id = c.user_id \
         WHERE c.movie_id = $1 ORDER BY c.created_at DESC",
    )
    .bind(movie_id)
    .fetch_all(&state.db)
    .await
    .unwrap_or_default();

    let comments: Vec<Value> = rows
        .iter()
        .map(|r| {
            json!({
                "id":                  r.try_get::<Uuid, _>("id").unwrap().to_string(),
                "content":             r.try_get::<String, _>("content").unwrap_or_default(),
                "created_at":          r.try_get::<chrono::DateTime<chrono::Utc>, _>("created_at")
                                         .ok()
                                         .map(|t| t.to_rfc3339()),
                "user_id":             r.try_get::<Uuid, _>("user_id").unwrap().to_string(),
                "username":            r.try_get::<String, _>("username").unwrap_or_default(),
                "profile_picture_url": r.try_get::<Option<String>, _>("profile_picture_url").unwrap_or(None),
            })
        })
        .collect();

    Ok(Json(json!({ "comments": comments })))
}

// ── POST /api/movies/:external_id/comments ────────────────────────────────────

pub async fn create_comment(
    headers: HeaderMap,
    Path(external_id): Path<String>,
    State(state): State<AppState>,
    Json(body): Json<CommentBody>,
) -> Result<Json<Value>, ApiError> {
    let claims = jwt::verify_from_headers(&headers)?;

    if body.content.trim().is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Content cannot be empty"})),
        ));
    }

    let user_id = claims
        .user_id
        .parse::<Uuid>()
        .map_err(|_| (StatusCode::UNAUTHORIZED, Json(json!({"error": "invalid_user"}))))?;

    // Ensure user still exists (e.g. DB was reset but client has old token)
    let user_exists = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)",
    )
    .bind(user_id)
    .fetch_one(&state.db)
    .await
    .map_err(|e| {
        eprintln!("DB error checking user: {e}");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?;
    if !user_exists {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({"error": "user_not_found", "message": "Session invalid, please log in again"})),
        ));
    }

    let movie_id = get_movie_uuid(&state.db, &external_id)
        .await
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(json!({"error": "Movie not found"}))))?;

    let row = sqlx::query(
        "INSERT INTO comments (user_id, movie_id, content) \
         VALUES ($1, $2, $3) \
         RETURNING id, content, created_at",
    )
    .bind(user_id)
    .bind(movie_id)
    .bind(body.content.trim())
    .fetch_one(&state.db)
    .await
    .map_err(|e| {
        eprintln!("DB error creating comment: {e}");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "db_error"})),
        )
    })?;

    Ok(Json(json!({
        "id":         row.try_get::<Uuid, _>("id").unwrap().to_string(),
        "content":    row.try_get::<String, _>("content").unwrap_or_default(),
        "created_at": row.try_get::<chrono::DateTime<chrono::Utc>, _>("created_at")
                        .ok()
                        .map(|t| t.to_rfc3339()),
    })))
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async fn get_movie_uuid(db: &sqlx::PgPool, external_id: &str) -> Option<Uuid> {
    sqlx::query_scalar("SELECT id FROM movies WHERE imdb_id = $1")
        .bind(external_id)
        .fetch_optional(db)
        .await
        .ok()
        .flatten()
}
