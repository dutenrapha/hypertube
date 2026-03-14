FROM alpine:3.20

RUN apk add --no-cache aria2

RUN mkdir -p /downloads

EXPOSE 6800

CMD ["sh", "-c", "exec aria2c --enable-rpc=true --rpc-listen-all=true --rpc-allow-origin-all=true --rpc-listen-port=6800 --rpc-secret=${ARIA2_RPC_SECRET} --dir=/downloads --log-level=warn"]
