# --- Build stage ---
FROM rust:latest AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends pkg-config libssl-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Cargo.toml ./
# Cache dependency layer by compiling a stub first
RUN mkdir src && echo 'fn main(){}' > src/main.rs && \
    cargo build --release && \
    rm -f target/release/hypertube target/release/deps/hypertube*

# Copy migrations BEFORE final build so sqlx::migrate!() can embed them
COPY migrations ./migrations
COPY src ./src
RUN cargo build --release

# --- Runtime stage ---
FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates libssl3 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -r app && useradd -r -g app app && \
    mkdir -p /uploads && chown app:app /uploads

WORKDIR /app
COPY --from=builder /app/target/release/hypertube .

USER app
EXPOSE 8000
CMD ["./hypertube"]
