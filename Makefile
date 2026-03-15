# Hypertube — atalhos comuns
# Use: make <target>

.PHONY: seed-oauth2 seed-user-insomnia seed-insomnia help

help:
	@echo "Targets:"
	@echo "  make seed-oauth2        — Cria só o cliente OAuth2 (insomnia-test)"
	@echo "  make seed-user-insomnia — Cria só o usuário isomniatest / 1insomniatest1"
	@echo "  make seed-insomnia      — Cria usuário + cliente OAuth2 para testar no Insomnia"

seed-user-insomnia:
	@echo "Criando usuário isomniatest (senha: 1insomniatest1)..."
	@test -f .env || (echo "Erro: arquivo .env não encontrado."; exit 1)
	@export $$(grep -E '^POSTGRES_USER=|^POSTGRES_DB=' .env | xargs) && \
	docker-compose exec postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" \
		-c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" \
		-c "INSERT INTO users (email, username, first_name, last_name, password_hash) VALUES ('isomniatest@test.local', 'isomniatest', 'Insomnia', 'Test', crypt('1insomniatest1', gen_salt('bf', 12))) ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, email = EXCLUDED.email, updated_at = NOW();"
	@echo "Usuário isomniatest criado. Use no Insomnia: username=isomniatest, password=1insomniatest1"

seed-oauth2:
	@echo "Criando cliente OAuth2 'insomnia-test' no banco..."
	@test -f .env || (echo "Erro: arquivo .env não encontrado."; exit 1)
	@export $$(grep -E '^POSTGRES_USER=|^POSTGRES_DB=' .env | xargs) && \
	docker-compose exec postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "INSERT INTO oauth2_clients (client_id, client_secret_hash, name) VALUES ('insomnia-test', encode(sha256('insomniasecret'::bytea), 'hex'), 'Insomnia Test') ON CONFLICT (client_id) DO NOTHING;"
	@echo "Cliente OAuth2 criado. No Insomnia: client_id=insomnia-test, client_secret=insomniasecret"

seed-insomnia: seed-user-insomnia seed-oauth2
	@echo ""
	@echo "Pronto para o Insomnia:"
	@echo "  - Token (password): client_id=insomnia-test, client_secret=insomniasecret, username=isomniatest, password=1insomniatest1"
