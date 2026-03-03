use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    Json,
};
use redis::AsyncCommands;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::Duration;
use uuid::Uuid;

use crate::{jwt, AppState};

const SEARCH_CACHE_TTL: u64 = 3600;  // 1 hour
const OMDB_CACHE_TTL: u64 = 86400;   // 24 hours
const HTTP_TIMEOUT: u64 = 10;

#[derive(Deserialize)]
pub struct SearchParams {
    q: Option<String>,
    page: Option<u32>,
    limit: Option<u32>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Movie {
    pub id: String,
    pub title: String,
    pub year: Option<String>,
    pub cover_url: Option<String>,
    pub magnet: Option<String>,
    pub source: String,
    pub imdb_rating: Option<String>,
    pub genre: Option<String>,
    pub watched: bool,
}

#[derive(Serialize)]
pub struct SearchResponse {
    pub movies: Vec<Movie>,
    pub page: u32,
    pub limit: u32,
    pub total: usize,
    pub has_next: bool,
}

fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(HTTP_TIMEOUT))
        .user_agent("Mozilla/5.0 (compatible; Hypertube/1.0)")
        .build()
        .unwrap_or_default()
}

// ── Redis helpers ─────────────────────────────────────────────────────────────

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

// ── Archive.org search ────────────────────────────────────────────────────────

async fn fetch_archive_btih(identifier: &str) -> Option<String> {
    let client = http_client();
    let url = format!("https://archive.org/metadata/{}/files", identifier);
    let resp = client.get(&url).send().await.ok()?;
    let json: Value = resp.json().await.ok()?;
    let files = json["result"].as_array()?;
    for file in files {
        if let Some(btih) = file["btih"].as_str() {
            if !btih.is_empty() {
                return Some(btih.to_string());
            }
        }
    }
    None
}

/// Escape a single token for Lucene (avoid breaking the query).
fn lucene_escape_word(word: &str) -> String {
    let s: String = word
        .chars()
        .filter(|c| !matches!(c, '\\' | '+' | '-' | '&' | '|' | '!' | '(' | ')' | '{' | '}' | '[' | ']' | '^' | '"' | '~' | '*' | '?' | ':'))
        .collect();
    let s = s.trim();
    if s.is_empty() {
        return String::new();
    }
    if s.contains(' ') {
        format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        s.replace('\\', "\\\\").replace('"', "\\\"")
    }
}

/// Build Archive.org Lucene query for non-empty search.
/// Uses general metadata search (like the archive.org website): (word1 AND word2 AND ...) AND mediatype:movies.
/// Avoids leading wildcards (title:*x*) which many Lucene backends reject or don't index well.
fn archive_search_query(query: &str) -> String {
    let trimmed = query.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let words: Vec<String> = trimmed
        .split_whitespace()
        .map(|s| lucene_escape_word(s.trim()))
        .filter(|s| !s.is_empty())
        .collect();
    if words.is_empty() {
        return String::new();
    }
    let term_part = if words.len() == 1 {
        words[0].clone()
    } else {
        format!("({})", words.join(" AND "))
    };
    format!("{} AND mediatype:movies", term_part)
}

async fn search_archive(query: &str) -> Vec<Movie> {
    let client = http_client();

    const ROWS: u32 = 50;
    let url = if query.is_empty() {
        format!("https://archive.org/advancedsearch.php?q=mediatype%3Amovies&fl[]=identifier,title,year&rows={}&output=json&sort[]=-downloads", ROWS)
    } else {
        let q = archive_search_query(query);
        format!(
            "https://archive.org/advancedsearch.php?q={}&fl[]=identifier,title,year&rows={}&output=json&sort[]=-downloads",
            urlencoding::encode(&q),
            ROWS
        )
    };

    let resp = match client.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("Archive.org request failed: {e}");
            return vec![];
        }
    };

    let json: Value = match resp.json().await {
        Ok(j) => j,
        Err(e) => {
            eprintln!("Archive.org JSON parse failed: {e}");
            return vec![];
        }
    };

    let docs = match json["response"]["docs"].as_array() {
        Some(d) => d.clone(),
        None => return vec![],
    };

    let items: Vec<(String, String, Option<String>)> = docs
        .iter()
        .filter_map(|doc| {
            let id = doc["identifier"].as_str()?.to_string();
            let title = doc["title"]
                .as_str()
                .unwrap_or(&id)
                .to_string();
            let year = doc["year"]
                .as_str()
                .map(|y| y.to_string())
                .or_else(|| doc["year"].as_i64().map(|y| y.to_string()));
            if id.is_empty() { None } else { Some((id, title, year)) }
        })
        .collect();

    let limited: Vec<_> = items.iter().take(ROWS as usize).collect();
    let btih_futures: Vec<_> = limited
        .iter()
        .map(|(id, _, _)| {
            let id = id.clone();
            async move { fetch_archive_btih(&id).await }
        })
        .collect();

    let btihs: Vec<Option<String>> = futures_util::future::join_all(btih_futures).await;

    limited
        .iter()
        .zip(btihs.iter())
        .map(|((id, title, year), btih)| {
            let magnet = btih.as_ref().map(|h| {
                format!(
                    "magnet:?xt=urn:btih:{}&dn={}&tr=http://bt1.archive.org:6969/announce&ws=https://archive.org/download/{}/",
                    h,
                    urlencoding::encode(title),
                    urlencoding::encode(id)
                )
            });
            Movie {
                id: id.clone(),
                title: title.clone(),
                year: year.clone(),
                cover_url: Some(format!("https://archive.org/services/img/{}", id)),
                magnet,
                source: "archive.org".to_string(),
                imdb_rating: None,
                genre: None,
                watched: false,
            }
        })
        .collect()
}

// ── PublicDomainTorrents.info search ─────────────────────────────────────────

fn parse_pdt_html(html: &str, query: &str) -> Vec<Movie> {
    let re = match Regex::new(
        r#"href="nshowmovie\.html\?movieid=(\d+)"[^>]*>([^<]+)</a>"#,
    ) {
        Ok(r) => r,
        Err(_) => return vec![],
    };

    let query_lower = query.to_lowercase();

    re.captures_iter(html)
        .filter_map(|cap| {
            let movie_id = cap.get(1)?.as_str().to_string();
            let raw_title = cap.get(2)?.as_str().trim().to_string();
            if raw_title.len() < 2 || raw_title.contains('<') {
                return None;
            }
            Some((movie_id, raw_title))
        })
        .filter(|(_, title)| {
            query.is_empty() || title.to_lowercase().contains(&query_lower)
        })
        .map(|(id, title)| Movie {
            id: format!("pdt-{}", id),
            title: title.clone(),
            year: None,
            cover_url: Some(format!(
                "https://www.publicdomaintorrents.info/nshowmovie.html?movieid={}",
                id
            )),
            magnet: Some(format!(
                "https://www.publicdomaintorrents.info/nshowmovie.html?movieid={}",
                id
            )),
            source: "publicdomaintorrents.info".to_string(),
            imdb_rating: None,
            genre: None,
            watched: false,
        })
        .collect()
}

async fn search_publicdomaintorrents(query: &str) -> Vec<Movie> {
    let client = http_client();

    let cat_query = {
        let mut c = query.chars();
        match c.next() {
            None => String::new(),
            Some(f) => f.to_uppercase().to_string() + c.as_str(),
        }
    };

    let cat_url = format!(
        "https://www.publicdomaintorrents.info/nshowcat.html?category={}",
        urlencoding::encode(&cat_query)
    );
    let all_url =
        "https://www.publicdomaintorrents.info/nshowcat.html?category=ALL".to_string();

    let (cat_resp, all_resp) = tokio::join!(
        client.get(&cat_url).send(),
        client.get(&all_url).send()
    );

    let mut movies: Vec<Movie> = vec![];

    if let Ok(resp) = cat_resp {
        if resp.status().is_success() {
            if let Ok(html) = resp.text().await {
                let cat_movies = parse_pdt_html(&html, query);
                if !cat_movies.is_empty() {
                    movies.extend(cat_movies.into_iter().take(5));
                    return movies;
                }
            }
        }
    }

    if let Ok(resp) = all_resp {
        if let Ok(html) = resp.text().await {
            let filtered = parse_pdt_html(&html, query);
            if !filtered.is_empty() {
                movies.extend(filtered.into_iter().take(5));
                return movies;
            }

            let fallback = parse_pdt_html(&html, "");
            movies.extend(fallback.into_iter().take(5));
        }
    }

    movies
}

// ── OMDb enrichment ───────────────────────────────────────────────────────────
// Returns (imdb_rating, genre). Cache key uses "omdb2:" prefix to avoid
// conflicts with the old "omdb:" format that stored a plain string.

async fn get_omdb_data(
    conn: &mut redis::aio::ConnectionManager,
    title: &str,
) -> (Option<String>, Option<String>) {
    let api_key = std::env::var("OMDB_API_KEY").unwrap_or_default();
    if api_key.is_empty() {
        return (None, None);
    }

    let cache_key = format!("omdb2:{}", title.to_lowercase());
    if let Some(cached) = redis_get(conn, &cache_key).await {
        if let Ok(obj) = serde_json::from_str::<Value>(&cached) {
            let rating = obj["rating"].as_str()
                .filter(|r| *r != "N/A")
                .map(|r| r.to_string());
            let genre = obj["genre"].as_str()
                .filter(|g| *g != "N/A")
                .map(|g| g.to_string());
            return (rating, genre);
        }
        return (None, None);
    }

    let url = format!(
        "https://www.omdbapi.com/?t={}&apikey={}",
        urlencoding::encode(title),
        api_key
    );

    let client = http_client();
    let resp: Value = match client.get(&url).send().await {
        Ok(r) => r.json().await.unwrap_or(json!({})),
        Err(_) => return (None, None),
    };

    let rating = resp["imdbRating"]
        .as_str()
        .filter(|r| *r != "N/A")
        .map(|r| r.to_string());
    let genre = resp["Genre"]
        .as_str()
        .filter(|g| *g != "N/A")
        .map(|g| g.to_string());

    let cache_value = json!({
        "rating": rating.clone().unwrap_or_else(|| "N/A".to_string()),
        "genre":  genre.clone().unwrap_or_else(|| "N/A".to_string()),
    });
    redis_set(conn, &cache_key, &cache_value.to_string(), OMDB_CACHE_TTL).await;

    (rating, genre)
}

// ── GET /api/search ───────────────────────────────────────────────────────────

pub async fn search(
    headers: HeaderMap,
    Query(params): Query<SearchParams>,
    State(state): State<AppState>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let claims = jwt::verify_from_headers(&headers)?;

    let query = params.q.unwrap_or_default();
    let query = query.trim().to_string();
    let page  = params.page.unwrap_or(1).max(1);
    let limit = params.limit.unwrap_or(20).min(50).max(1);

    eprintln!("[search] request q={:?} page={} limit={}", query, page, limit);

    let mut conn = state.redis.clone();

    // Full-list cache (not user-specific — watched is injected later)
    let cache_key = format!("search:{}", query);
    let mut movies: Vec<Movie> = if let Some(cached) = redis_get(&mut conn, &cache_key).await {
        let parsed: Vec<Movie> = serde_json::from_str(&cached).unwrap_or_default();
        eprintln!("[search] cache HIT for q={:?} -> {} movies", query, parsed.len());
        parsed
    } else {
        eprintln!("[search] cache MISS for q={:?}, calling archive + pdt", query);
        let (archive_movies, pdt_movies) = tokio::join!(
            search_archive(&query),
            search_publicdomaintorrents(&query)
        );

        eprintln!(
            "[search] archive returned {} movies, pdt returned {} movies",
            archive_movies.len(),
            pdt_movies.len()
        );

        let mut all: Vec<Movie> = Vec::new();
        all.extend(archive_movies);
        all.extend(pdt_movies);

        // Fallback: when query is non-empty and external APIs return 0, fetch popular list
        // and filter by title (so e.g. "bananas" matches "About Bananas")
        if !query.is_empty() && all.is_empty() {
            eprintln!("[search] fallback: fetching popular list and filtering by title");
            let empty_key = "search:".to_string();
            let popular: Vec<Movie> = if let Some(cached) = redis_get(&mut conn, &empty_key).await {
                serde_json::from_str(&cached).unwrap_or_default()
            } else {
                let (arch, pdt) = tokio::join!(search_archive(""), search_publicdomaintorrents(""));
                let mut list: Vec<Movie> = Vec::new();
                list.extend(arch);
                list.extend(pdt);
                if !list.is_empty() {
                    if let Ok(s) = serde_json::to_string(&list) {
                        redis_set(&mut conn, &empty_key, &s, SEARCH_CACHE_TTL).await;
                    }
                }
                list
            };
            let q_lower = query.to_lowercase();
            all = popular
                .into_iter()
                .filter(|m| m.title.to_lowercase().contains(&q_lower))
                .collect();
            eprintln!("[search] fallback: {} movies after filtering by title", all.len());
        }

        // Enrich with OMDb data (rating + genre)
        for movie in &mut all {
            let (rating, genre) = get_omdb_data(&mut conn, &movie.title).await;
            movie.imdb_rating = rating;
            movie.genre = genre;
        }

        // Only cache non-empty results so a failed request (e.g. network error) doesn't overwrite good cache
        if !all.is_empty() {
            if let Ok(json_str) = serde_json::to_string(&all) {
                redis_set(&mut conn, &cache_key, &json_str, SEARCH_CACHE_TTL).await;
            }
        }

        all
    };

    // Inject watched status per user (never cached)
    if let Ok(user_uuid) = claims.user_id.parse::<Uuid>() {
        let watched_titles: Vec<String> = sqlx::query_scalar(
            "SELECT m.title FROM watched_movies wm \
             JOIN movies m ON m.id = wm.movie_id \
             WHERE wm.user_id = $1",
        )
        .bind(user_uuid)
        .fetch_all(&state.db)
        .await
        .unwrap_or_default();

        let watched_set: std::collections::HashSet<String> = watched_titles
            .into_iter()
            .map(|t| t.to_lowercase())
            .collect();

        for movie in &mut movies {
            movie.watched = watched_set.contains(&movie.title.to_lowercase());
        }
    }

    let total  = movies.len();
    let offset = ((page - 1) * limit) as usize;
    let page_movies: Vec<Movie> = movies.into_iter().skip(offset).take(limit as usize).collect();
    let has_next = offset + page_movies.len() < total;

    eprintln!(
        "[search] response total={} offset={} returning {} movies has_next={}",
        total,
        offset,
        page_movies.len(),
        has_next
    );

    Ok(Json(json!(SearchResponse {
        movies: page_movies,
        page,
        limit,
        total,
        has_next,
    })))
}
