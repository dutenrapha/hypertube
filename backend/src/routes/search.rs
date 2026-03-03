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

use crate::{jwt, AppState};

const SEARCH_CACHE_TTL: u64 = 3600;  // 1 hour
const OMDB_CACHE_TTL: u64 = 86400;   // 24 hours
const HTTP_TIMEOUT: u64 = 10;

#[derive(Deserialize)]
pub struct SearchParams {
    q: Option<String>,
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

async fn search_archive(query: &str) -> Vec<Movie> {
    let client = http_client();

    let url = if query.is_empty() {
        "https://archive.org/advancedsearch.php?q=mediatype%3Amovies&fl[]=identifier,title,year&rows=20&output=json&sort[]=-downloads".to_string()
    } else {
        format!(
            "https://archive.org/advancedsearch.php?q={}&fl[]=identifier,title,year&rows=20&output=json&mediatype=movies",
            urlencoding::encode(query)
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

    // Extract basic info
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

    // Fetch btih for each item in parallel (limit to 10 to avoid overloading)
    let limited: Vec<_> = items.iter().take(10).collect();
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
            // Filter HTML entities and short titles
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
        })
        .collect()
}

async fn search_publicdomaintorrents(query: &str) -> Vec<Movie> {
    let client = http_client();

    // Capitalise first letter for category match (e.g. "horror" → "Horror")
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

    // Fetch category page and ALL page in parallel
    let (cat_resp, all_resp) = tokio::join!(
        client.get(&cat_url).send(),
        client.get(&all_url).send()
    );

    // Try category-based results first
    let mut movies: Vec<Movie> = vec![];

    if let Ok(resp) = cat_resp {
        if resp.status().is_success() {
            if let Ok(html) = resp.text().await {
                // Pass empty query so all results from the category page are included
                let cat_movies = parse_pdt_html(&html, "");
                if !cat_movies.is_empty() {
                    movies.extend(cat_movies.into_iter().take(5));
                    return movies;
                }
            }
        }
    }

    // Fall back to ALL page filtered by title
    if let Ok(resp) = all_resp {
        if let Ok(html) = resp.text().await {
            let filtered = parse_pdt_html(&html, query);
            if !filtered.is_empty() {
                movies.extend(filtered.into_iter().take(5));
                return movies;
            }

            // Last resort: return top 5 from ALL listing so PDT always contributes
            let fallback = parse_pdt_html(&html, "");
            movies.extend(fallback.into_iter().take(5));
        }
    }

    movies
}

// ── OMDb enrichment ───────────────────────────────────────────────────────────

async fn get_omdb_rating(
    conn: &mut redis::aio::ConnectionManager,
    title: &str,
) -> Option<String> {
    let api_key = std::env::var("OMDB_API_KEY").unwrap_or_default();
    if api_key.is_empty() {
        return None;
    }

    let cache_key = format!("omdb:{}", title.to_lowercase());
    if let Some(cached) = redis_get(conn, &cache_key).await {
        return if cached == "N/A" { None } else { Some(cached) };
    }

    let url = format!(
        "https://www.omdbapi.com/?t={}&apikey={}",
        urlencoding::encode(title),
        api_key
    );

    let client = http_client();
    let json: Value = match client.get(&url).send().await {
        Ok(r) => r.json().await.unwrap_or(json!({})),
        Err(_) => return None,
    };

    let rating = json["imdbRating"]
        .as_str()
        .filter(|r| *r != "N/A")
        .map(|r| r.to_string());

    // Cache even N/A results to avoid repeated requests
    let cache_value = rating.clone().unwrap_or_else(|| "N/A".to_string());
    redis_set(conn, &cache_key, &cache_value, OMDB_CACHE_TTL).await;

    rating
}

// ── GET /api/search ───────────────────────────────────────────────────────────

pub async fn search(
    headers: HeaderMap,
    Query(params): Query<SearchParams>,
    State(state): State<AppState>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    jwt::verify_from_headers(&headers)?;

    let query = params.q.unwrap_or_default();
    let query = query.trim().to_string();

    let mut conn = state.redis.clone();

    // Check cache
    let cache_key = format!("search:{}", query);
    if let Some(cached) = redis_get(&mut conn, &cache_key).await {
        if let Ok(movies) = serde_json::from_str::<Vec<Movie>>(&cached) {
            return Ok(Json(json!(movies)));
        }
    }

    // Fetch from both sources in parallel
    let (archive_movies, pdt_movies) = tokio::join!(
        search_archive(&query),
        search_publicdomaintorrents(&query)
    );

    let mut movies: Vec<Movie> = Vec::new();
    movies.extend(archive_movies);
    movies.extend(pdt_movies);

    // Enrich with OMDb ratings (sequentially to avoid hammering the API)
    for movie in &mut movies {
        movie.imdb_rating = get_omdb_rating(&mut conn, &movie.title).await;
    }

    // Cache the combined results
    if let Ok(json_str) = serde_json::to_string(&movies) {
        redis_set(&mut conn, &cache_key, &json_str, SEARCH_CACHE_TTL).await;
    }

    Ok(Json(json!(movies)))
}
