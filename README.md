# Hypertube

A web application that lets users **search** for videos from external sources and **watch** them in-browser. Videos are downloaded on the server via the **BitTorrent** protocol and **streamed** as soon as enough data is available. The stack is built for security, clarity, and maintainability.

---

## Overview

- **Purpose**: Search and stream videos; the player is integrated into the site; downloads use BitTorrent (no webtorrent/peerflix-style libraries—downloads run server-side with aria2).
- **Stack**: Backend in **Rust (Axum)**, frontend in **React (Vite)** with TypeScript, **PostgreSQL**, **Redis**, **aria2c** for torrents, **Mailhog** for local email, all orchestrated with **Docker Compose**.
- **Author file**: An `author` file must be present at the **root of the repository** (as required for the project). It should list the contributors.

---

## Architecture

### High-level

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│   Browser   │────▶│   Frontend  │────▶│   Backend    │
│  (React)    │     │  (Vite)     │     │  (Axum)      │
└─────────────┘     └─────────────┘     └──────┬───────┘
       │                    │                   │
       │                    │              ┌────┴────┐
       │                    │              │ Postgres│
       │                    │              │ Redis   │
       │                    │              │ aria2c  │
       │                    │              │ Mailhog │
       │                    │              └─────────┘
       │                    │
       └────────────────────┴──▶ External: Archive.org, PublicDomainTorrents, OMDb, OAuth 42, Google
```

- **Frontend** (port 3000): SPA with React Router; all API calls go to the backend (or via proxy in dev). Header, main content, and footer structure the layout; the app is responsive for mobile and small resolutions.
- **Backend** (port 8000): REST API; JWT for web auth; optional OAuth2 server for third-party clients (e.g. Insomnia). Serves uploads, subtitles, and video streams (with Range support).
- **PostgreSQL**: Users, OAuth accounts, password-reset tokens, movies, watched_movies, comments, OAuth2 clients/tokens. Parameterized queries only—no string concatenation in SQL.
- **Redis**: Caching for search results and OMDb data to reduce external calls and speed up the library view.
- **aria2c**: Handles magnet links; downloads in the background; the backend polls status and serves the file (or converted MP4) when ready.
- **Mailhog**: Local SMTP for password-reset emails during development.

### Security

- **Passwords**: Stored only as **bcrypt** hashes (never plain text). Cost factor 12 for registration and reset.
- **Input**: All registration, login, profile, and upload forms are **validated** (length, format, MIME type for profile pictures). Invalid input returns 4xx with clear messages.
- **SQL**: Every query uses **parameterized statements** (e.g. `$1`, `$2`); user input is never concatenated into SQL, so **SQL injection** is not possible.
- **Secrets**: Credentials, API keys, and env vars live in `.env` (gitignored). No secrets in the repo.
- **Headers**: Responses include `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`.

---

## How It Works (Feature by Feature)

### Users and authentication

- **Registration**: Users provide email, username, first name, last name, password, and optionally a profile picture (multipart). Password rules: minimum length, uppercase, lowercase, digit. Email and username are unique. Profile picture is validated (type and size) and stored under `/uploads`.
- **Login**: Username or email + password; JWT is returned and stored on the client for subsequent API calls.
- **OAuth**: Two strategies—**42** and **Google**. User is sent to the provider, then callback creates or links the account and redirects back to the frontend with a JWT. No plain-text password for OAuth-only users.
- **Password reset**: “Forgot password” asks for email; a secure token is generated, stored as SHA256 hash with expiry (e.g. 1 hour), and a link is sent by email (SMTP). The reset page submits token + new password; password is re-validated and re-hashed with bcrypt.
- **Logout**: One-click logout from any page; client discards the JWT.
- **Profile**: Connected users can edit email, username, first name, last name, profile picture. They can view other users’ profiles (picture and info); email stays private. Preferred language is supported (default English).

### Library (search and list)

- **Access**: The library (search and thumbnails) is **only available to authenticated users**; unauthenticated requests are rejected.
- **Default view**: When no search is performed, the app shows **popular** videos from the external sources (e.g. sorted by downloads on Archive.org and similar logic for the second source). This list is cached in Redis.
- **Thumbnails**: Each item shows title, production year (if available), score (e.g. from OMDb), and a cover image. **Watched** vs **unwatched** is clearly differentiated (e.g. visual state) using the `watched_movies` table per user.
- **Pagination and sorting**: The list is **paginated**; the next page is loaded **asynchronously** (e.g. on scroll), without a separate “next page” link. The list can be **sorted/filtered** by criteria such as name, year, rating, genre.
- **Search**: The search engine queries **at least two separate external sources** (e.g. **Archive.org** and **PublicDomainTorrents**). Results are limited to videos and displayed as thumbnails **sorted by name**. OMDb (optional) enriches results with rating and genre; all external data is cached in Redis.

### Video playback

- **Access**: The video section (player and details) is **only available to authenticated users**.
- **Information**: Besides the player, the page shows summary, cast (e.g. director, main cast), year, length, rating, cover, and any other collected metadata.
- **Comments**: Users see the list of comments for the video and can **post a new comment**. Comments are stored in the DB and displayed with author and date.
- **Download and stream**: Starting a video **launches the torrent on the server** (aria2) in a **non-blocking** way. As soon as enough data is available for **smooth playback**, the player **streams** the video (Range requests). The backend does not use libraries that stream directly from torrent to browser; it uses aria2 to download and then serves the file (or a converted one).
- **Conversion**: If the file is not natively readable by the browser (e.g. **MKV**), it is **converted on the fly** (e.g. FFmpeg to MP4 with faststart). Conversion runs in the background; the UI shows a “converting” state until the MP4 is ready.
- **Persistence and cleanup**: A fully downloaded video is **saved on the server**. If a video has **not been watched for a month**, it is **deleted** (scheduled cleanup job). Opening an already-downloaded video **does not re-download**; it streams from the existing file.
- **Subtitles**: When **English** subtitles are available (e.g. from the source or OpenSubtitles), they are **downloaded and offered** in the player. If the video language differs from the user’s preferred language and subtitles exist, they are also fetched and selectable.

### API (RESTful + OAuth2)

- The backend exposes a **RESTful API** with **OAuth2** (e.g. `POST /oauth/token` with client credentials or password grant). Token in header: `Authorization: Bearer <token>`.
- **Authenticated** users can read/update profiles (`GET/PATCH /users/:id`), access the front page movies (`GET /movies`), get movie details (`GET /movies/:id`), and manage comments (`GET/POST /comments`, `GET/PATCH/DELETE /comments/:id`, `POST /movies/:movie_id/comments`). Unsupported methods or routes return the **appropriate HTTP status** (e.g. 404, 405).
- Documentation and a ready-to-import Insomnia collection are provided (see `docs/INSOMNIA_OAUTH2.md` and `insomnia_oauth2_api.json`).

---

## Good practices

- **Layout**: Clear **header** (logo, library, profile, logout), **main** content area (search, list, or video), and **footer**. Navigation is intuitive so a new user can quickly understand how to register, log in, and search.
- **Responsive**: The site is **presentable on mobile and small resolutions** (responsive layout and controls).
- **Compatibility**: The frontend and streaming are tested to work on **recent Firefox and Chrome**.
- **Stability**: No errors, warnings, or notices in the browser console in normal use; server responses avoid 5xx unless there is a real server failure. All validations and error paths are handled so the app stays stable and predictable.

---

## Running the project

1. **Prerequisites**: Docker and Docker Compose. For local backend/frontend dev: Rust toolchain, Node.js, and (optionally) a local Postgres/Redis or use Docker for them.

2. **Configuration**: Copy `.env.example` to `.env` and set at least:
   - `POSTGRES_PASSWORD`, `DATABASE_URL`, `JWT_SECRET`
   - `FRONTEND_URL` (e.g. `http://localhost:3000`)
   - OAuth: `OAUTH_42_*`, `OAUTH_GOOGLE_*` (and matching redirect URIs)
   - `ARIA2_RPC_SECRET`, SMTP (or Mailhog defaults)
   - Optional: `OMDB_API_KEY` for rating/genre enrichment

3. **With Docker** (full stack):
   ```bash
   docker-compose up -d
   ```
   Frontend: http://localhost:3000 — Backend: http://localhost:8000

4. **OAuth2 API testing** (e.g. Insomnia): Create a user and OAuth2 client in the DB, then use the Insomnia collection. See `docs/INSOMNIA_OAUTH2.md` and `make seed-insomnia`.

5. **Author file**: Ensure an `author` file exists at the **root** of the repo with the team members’ identifiers (e.g. one per line), as required by the project rules.

---

## Summary

Hypertube is a full-stack video search and streaming app: secure user management (registration, OAuth 42/Google, password reset), a protected library with two-source search and rich thumbnails (watched/unwatched, pagination, sorting), and a video section with metadata, comments, server-side torrent download (aria2), streaming with Range support, MKV→MP4 conversion, subtitle support, and automatic cleanup of unwatched files. The RESTful API with OAuth2 allows external clients to access the same data in a controlled way. The architecture and this README are designed to make it easy for anyone to understand how each requirement is implemented and how the system behaves end to end.
