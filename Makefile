# =============================================================================
# MarketFlow AI — Makefile
# =============================================================================

.PHONY: proto backend frontend brain docker-up docker-down clean install

# ── Proto compilation ─────────────────────────────────────────────────────────
# Generates:
#   - Go stubs → backend/proto/
#   - Python stubs → ai-brain/proto/
proto:
	@echo "Compiling protobuf..."
	mkdir -p backend/proto ai-brain/proto
	protoc -I infra/proto \
		--go_out=backend/proto \
		--go_opt=paths=source_relative \
		--go-grpc_out=backend/proto \
		--go-grpc_opt=paths=source_relative \
		infra/proto/signals.proto
	cd ai-brain && python3 -m grpc_tools.protoc \
		-I ../infra/proto \
		--python_out=proto \
		--grpc_python_out=proto \
		../infra/proto/signals.proto
	@echo "Done."

# ── Run services locally ──────────────────────────────────────────────────────
backend:
	cd backend && go run cmd/server/main.go

frontend:
	cd frontend && npm run dev

brain:
	cd ai-brain && python main.py

# ── Docker ────────────────────────────────────────────────────────────────────
docker-up:
	docker-compose up --build

docker-down:
	docker-compose down

# ── Install dependencies ──────────────────────────────────────────────────────
install:
	cd frontend && npm install
	cd ai-brain && pip install -r requirements.txt --break-system-packages
	cd backend && go mod tidy

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -f infra/db/*.db infra/db/*.db-shm infra/db/*.db-wal
	rm -rf frontend/dist frontend/node_modules
	rm -rf ai-brain/__pycache__ ai-brain/.venv
