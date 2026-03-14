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
FROM ubuntu:24.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates libssl3 ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /uploads /downloads

WORKDIR /app
COPY --from=builder /app/target/release/hypertube .

EXPOSE 8000
CMD ["./hypertube"]
