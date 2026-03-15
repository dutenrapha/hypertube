-- Usuário para testar a API OAuth2 no Insomnia.
-- username: isomniatest
-- password: 1insomniatest1
-- Requer extensão pgcrypto para crypt()/gen_salt() (bcrypt).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO users (email, username, first_name, last_name, password_hash)
VALUES (
  'isomniatest@test.local',
  'isomniatest',
  'Insomnia',
  'Test',
  crypt('1insomniatest1', gen_salt('bf', 12))
)
ON CONFLICT (username) DO UPDATE SET
  password_hash = EXCLUDED.password_hash,
  email         = EXCLUDED.email,
  updated_at    = NOW();
