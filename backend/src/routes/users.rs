use axum::{http::HeaderMap, http::StatusCode, Json};
use serde_json::{json, Value};

use crate::jwt;

pub async fn list_users(
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let claims = jwt::verify_from_headers(&headers)?;

    Ok(Json(json!({
        "authenticated_as": {
            "user_id":  claims.user_id,
            "username": claims.username,
        }
    })))
}
