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

/// Paginated Archive.org search.
///
/// Returns `(movies, num_found)` where `num_found` is the total number of
/// matches reported by Archive.org (across all pages). `page` is 1-based.
async fn search_archive(query: &str, page: u32, rows: u32) -> (Vec<Movie>, u64) {
    let client = http_client();

    let url = if query.is_empty() {
        format!(
            "https://archive.org/advancedsearch.php?q=mediatype%3Amovies&fl[]=identifier,title,year&rows={}&page={}&output=json&sort[]=-downloads",
            rows, page
        )
    } else {
        let q = archive_search_query(query);
        format!(
            "https://archive.org/advancedsearch.php?q={}&fl[]=identifier,title,year&rows={}&page={}&output=json&sort[]=-downloads",
            urlencoding::encode(&q),
            rows,
            page
        )
    };

    let resp = match client.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("Archive.org request failed: {e}");
            return (vec![], 0);
        }
    };

    let json: Value = match resp.json().await {
        Ok(j) => j,
        Err(e) => {
            eprintln!("Archive.org JSON parse failed: {e}");
            return (vec![], 0);
        }
    };

    let num_found = json["response"]["numFound"].as_u64().unwrap_or(0);

    let docs = match json["response"]["docs"].as_array() {
        Some(d) => d.clone(),
        None => return (vec![], num_found),
    };

    let movies: Vec<Movie> = docs
        .iter()
        .filter_map(|doc| {
            let id = doc["identifier"].as_str()?.to_string();
            if id.is_empty() {
                return None;
            }
            let title = doc["title"].as_str().unwrap_or(&id).to_string();
            let year = doc["year"]
                .as_str()
                .map(|y| y.to_string())
                .or_else(|| doc["year"].as_i64().map(|y| y.to_string()));
            Some(Movie {
                cover_url: Some(format!("https://archive.org/services/img/{}", id)),
                id,
                title,
                year,
                magnet: None,
                source: "archive.org".to_string(),
                imdb_rating: None,
                genre: None,
                watched: false,
            })
        })
        .collect();

    (movies, num_found)
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

/// Cached page payload. Stored per (query, page, limit) tuple in Redis.
/// `archive_total` is needed to compute `has_next` without re-hitting Archive.org.
#[derive(Serialize, Deserialize)]
struct CachedPage {
    movies: Vec<Movie>,
    archive_total: u64,
}

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

    // Cache key now includes page+limit so each page is cached independently.
    let cache_key = format!("search:{}:p{}:l{}", query, page, limit);

    let cached: Option<CachedPage> = redis_get(&mut conn, &cache_key)
        .await
        .and_then(|s| serde_json::from_str(&s).ok());

    let (mut movies, archive_total) = if let Some(c) = cached {
        eprintln!(
            "[search] cache HIT for {} -> {} movies (archive_total={})",
            cache_key,
            c.movies.len(),
            c.archive_total
        );
        (c.movies, c.archive_total)
    } else {
        eprintln!("[search] cache MISS for {}", cache_key);

        let mut all: Vec<Movie> = Vec::new();
        let archive_total: u64;

        if page == 1 {
            // Page 1: PDT (secondary source, page-1 only) + Archive.org page 1 in parallel.
            // We always ask Archive.org for `limit` rows so we have something to fall back
            // to even if PDT alone fills the whole page.
            let (pdt_movies, (archive_movies, total)) = tokio::join!(
                search_publicdomaintorrents(&query),
                search_archive(&query, 1, limit)
            );
            archive_total = total;

            eprintln!(
                "[search] page=1 pdt={} archive={} archive_total={}",
                pdt_movies.len(),
                archive_movies.len(),
                archive_total
            );

            // PDT first (up to limit), then Archive fills the rest, capped at `limit`.
            let pdt_take = std::cmp::min(pdt_movies.len(), limit as usize);
            all.extend(pdt_movies.into_iter().take(pdt_take));
            let remaining = (limit as usize).saturating_sub(pdt_take);
            all.extend(archive_movies.into_iter().take(remaining));
        } else {
            // Pages > 1: only Archive.org. PDT is too small to paginate.
            let (archive_movies, total) = search_archive(&query, page, limit).await;
            archive_total = total;
            eprintln!(
                "[search] page={} archive={} archive_total={}",
                page,
                archive_movies.len(),
                archive_total
            );
            all.extend(archive_movies);
        }

        // Fallback (page 1 only): when a non-empty query returns 0 from external APIs,
        // try filtering the popular list by title (so "bananas" matches "About Bananas").
        if !query.is_empty() && all.is_empty() && page == 1 {
            eprintln!("[search] fallback: filtering popular list by title");
            let popular_key = format!("search::p1:l{}", limit);
            let popular: Vec<Movie> =
                if let Some(cached) = redis_get(&mut conn, &popular_key).await {
                    serde_json::from_str::<CachedPage>(&cached)
                        .map(|c| c.movies)
                        .unwrap_or_default()
                } else {
                    let (popular, _) = search_archive("", 1, limit).await;
                    popular
                };
            let q_lower = query.to_lowercase();
            all = popular
                .into_iter()
                .filter(|m| m.title.to_lowercase().contains(&q_lower))
                .collect();
            eprintln!("[search] fallback: {} movies after filtering by title", all.len());
        }

        // Enrich with OMDb data (rating + genre). OMDb itself is cached per-title in Redis
        // for 24h, so subsequent pages on the same query stay fast.
        for movie in &mut all {
            let (rating, genre) = get_omdb_data(&mut conn, &movie.title).await;
            movie.imdb_rating = rating;
            movie.genre = genre;
        }

        // Cache the page payload. Empty results get a short TTL so transient external
        // failures don't permanently mask a query.
        let ttl = if all.is_empty() { 300 } else { SEARCH_CACHE_TTL };
        let payload = CachedPage {
            movies: all.clone(),
            archive_total,
        };
        if let Ok(json_str) = serde_json::to_string(&payload) {
            redis_set(&mut conn, &cache_key, &json_str, ttl).await;
        }

        (all, archive_total)
    };

    // Inject watched status per user (never cached because it's user-specific).
    // Match by imdb_id (= external id used in the search payload), not by title:
    // titles are noisy and ambiguous, while imdb_id is what we persist when the
    // user opens / streams the movie (movies.imdb_id <- external_id).
    if let Ok(user_uuid) = claims.user_id.parse::<Uuid>() {
        let watched_imdb_ids: Vec<String> = sqlx::query_scalar(
            "SELECT m.imdb_id FROM watched_movies wm \
             JOIN movies m ON m.id = wm.movie_id \
             WHERE wm.user_id = $1 AND m.imdb_id IS NOT NULL",
        )
        .bind(user_uuid)
        .fetch_all(&state.db)
        .await
        .unwrap_or_default();

        let watched_set: std::collections::HashSet<String> =
            watched_imdb_ids.into_iter().collect();

        for movie in &mut movies {
            movie.watched = watched_set.contains(&movie.id);
        }
    }

    // `has_next` is true whenever Archive.org reports more matches than we've
    // already covered through the pages so far. We use `page * limit` as an
    // upper bound on what we've shown from Archive — it's conservative on page 1
    // (where some PDT items took some of the slots) but is correct in the sense
    // that if it's true, there are still Archive items left to show.
    let archive_offset_after_this_page = (page as u64) * (limit as u64);
    let has_next = archive_total > archive_offset_after_this_page;

    // `total` is the user-visible total: PDT only contributes on page 1, but
    // it's a small number; reporting `archive_total` alone is accurate enough
    // for clients (the frontend uses `has_next` for the infinite-scroll trigger).
    let total = archive_total as usize;

    eprintln!(
        "[search] response page={} returning {} movies archive_total={} has_next={}",
        page,
        movies.len(),
        archive_total,
        has_next
    );

    Ok(Json(json!(SearchResponse {
        movies,
        page,
        limit,
        total,
        has_next,
    })))
}
