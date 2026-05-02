FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 GOOS=linux go build -o marketflow-server ./cmd/server

FROM alpine:3.19
RUN apk add --no-cache ca-certificates sqlite-libs
WORKDIR /app
COPY --from=builder /app/marketflow-server .
EXPOSE 8080 8081 50051
CMD ["./marketflow-server"]
