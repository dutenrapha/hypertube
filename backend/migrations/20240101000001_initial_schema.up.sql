CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    email               TEXT        NOT NULL UNIQUE,
    username            TEXT        NOT NULL UNIQUE,
    first_name          TEXT        NOT NULL,
    last_name           TEXT        NOT NULL,
    -- NULL allowed for OAuth-only accounts; when set must be a bcrypt hash
    password_hash       TEXT        CHECK (password_hash IS NULL OR password_hash LIKE '$2%'),
    profile_picture_url TEXT,
    preferred_language  TEXT        NOT NULL DEFAULT 'en',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider         TEXT        NOT NULL CHECK (provider IN ('42', 'google')),
    provider_user_id TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_user_id)
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT        NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS movies (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT        NOT NULL,
    year            INTEGER,
    imdb_id         TEXT        UNIQUE,
    imdb_rating     NUMERIC(3,1),
    genre           TEXT,
    length_minutes  INTEGER,
    cover_url       TEXT,
    torrent_magnet  TEXT,
    torrent_hash    TEXT        UNIQUE,
    file_path       TEXT,
    downloaded_at   TIMESTAMPTZ,
    last_watched_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watched_movies (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_id   UUID        NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    watched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, movie_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_id   UUID        NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    content    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth2_clients (
    id                 UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id          TEXT        NOT NULL UNIQUE,
    client_secret_hash TEXT        NOT NULL,
    name               TEXT        NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth2_tokens (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id  UUID        NOT NULL REFERENCES oauth2_clients(id) ON DELETE CASCADE,
    user_id    UUID        REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT        NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
