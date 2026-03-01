# --- Build stage ---
FROM rust:1.82-alpine AS builder

RUN apk add --no-cache musl-dev

WORKDIR /app
COPY Cargo.toml ./
# Cache deps layer: build a dummy main first
RUN mkdir src && echo 'fn main(){}' > src/main.rs && \
    cargo build --release && \
    rm -f target/release/hypertube target/release/deps/hypertube*

COPY src ./src
RUN cargo build --release

# --- Runtime stage ---
FROM alpine:3.20

RUN addgroup -S app && adduser -S app -G app

WORKDIR /app
COPY --from=builder /app/target/release/hypertube .

USER app
EXPOSE 8000
CMD ["./hypertube"]
