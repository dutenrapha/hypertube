# Insomnia – OAuth2 API (Card 16)

## Como rodar (rápido)

1. **Criar usuário de teste e cliente OAuth2** (uma vez). Com o backend/Postgres rodando (ex.: `docker-compose up -d`), na raiz do projeto:

   ```bash
   make seed-insomnia
   ```

   Isso cria o usuário `isomniatest` (senha `1insomniatest1`) e o cliente OAuth2 `insomnia-test` (secret `insomniasecret`). Para criar só o usuário: `make seed-user-insomnia`. Para criar só o cliente: `make seed-oauth2`.

2. **No Insomnia**, no environment **Base**, preencha:
   - **base_url**: `http://localhost:8000`
   - **client_id**: `insomnia-test`
   - **client_secret**: `insomniasecret`
   - **username**: `isomniatest`
   - **password**: `1insomniatest1`

3. Envie **POST Token (password)**. Se der certo, copie o `access_token` da resposta e cole em **user_token** no environment. Use esse token nas outras requisições (GET users, GET movies, etc.).

O erro `invalid_client` aparece quando o **client_id** ou **client_secret** não existem/no banco ou estão errados — não é por causa do usuário/senha.

---

## Importar a collection

1. Abra o Insomnia.
2. **Application** → **Import/Export** → **Import Data** → **From File**.
3. Selecione o arquivo `insomnia_oauth2_api.json` na raiz do projeto.
4. A workspace **Hypertube OAuth2 API (Card 16)** será criada com todas as requisições.

## Configurar o ambiente

1. Selecione o ambiente **Base** (canto superior esquerdo).
2. Preencha no environment:
   - **base_url**: `http://localhost:8000` (ou `http://127.0.0.1:8000` se o backend estiver no host).
   - **client_id** e **client_secret**: do cliente OAuth2 que você registrou no banco (veja abaixo).
   - **username** e **password**: de um usuário existente (para token tipo `password`).
   - **user_token**: preencha manualmente depois de chamar **POST Token (password)** e copiar o `access_token` da resposta, ou use o recurso do Insomnia para enviar o token da resposta para o env.

Para endpoints que usam `user_id`, `movie_id` ou `comment_id`, preencha esses campos no environment com os UUIDs retornados nas listagens (GET users, GET movies, GET comments).

## Criar um cliente OAuth2 no banco

O backend espera um registro em `oauth2_clients` com `client_id` (texto), `client_secret_hash` (SHA256 do segredo em hex) e `name`.

Exemplo para cliente `insomnia-test` com segredo `mysecret`:

```sql
-- SHA256('mysecret') em hex. No psql ou em outro cliente:
INSERT INTO oauth2_clients (client_id, client_secret_hash, name)
VALUES (
  'insomnia-test',
  encode(sha256('mysecret'::bytea), 'hex'),
  'Insomnia Test'
);
```

Se preferir gerar o hash fora do SQL (ex.: `echo -n mysecret | sha256sum`), use o valor em hex no lugar de `encode(sha256('mysecret'::bytea), 'hex')`.

Depois, no Insomnia, use **client_id** = `insomnia-test` e **client_secret** = `mysecret`.

## Ordem sugerida para testar

1. **POST Token (password)** → copiar `access_token` para **user_token** no environment.
2. **GET List users** → copiar um `id` para **user_id** (para GET User by ID e PATCH Update user).
3. **GET List movies** → copiar um `id` para **movie_id** (para GET Movie by ID e criar comentários).
4. **POST Comment on movie** ou **POST Create comment** → copiar o `id` retornado para **comment_id** (para GET/PATCH/DELETE comment).

Endpoints que alteram dados (PATCH user, POST/PATCH/DELETE comment) exigem token obtido com **password** (user-bound). Os de leitura aceitam token **client_credentials** ou **password**.
