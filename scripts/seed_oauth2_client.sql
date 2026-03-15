-- Cliente OAuth2 para testar no Insomnia (Card 16).
-- client_id: insomnia-test
-- client_secret: insomniasecret
-- O backend armazena SHA256(client_secret) em hex.
INSERT INTO oauth2_clients (client_id, client_secret_hash, name)
VALUES (
  'insomnia-test',
  encode(sha256('insomniasecret'::bytea), 'hex'),
  'Insomnia Test'
)
ON CONFLICT (client_id) DO NOTHING;
