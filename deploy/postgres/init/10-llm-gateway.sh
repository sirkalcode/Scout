#!/bin/sh
# Runs once, when the database volume is first created. Each service gets its own role and schema.
set -eu

psql -v ON_ERROR_STOP=1 -v password="$LLM_DB_PASSWORD" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE llm_gateway LOGIN PASSWORD :'password';
CREATE SCHEMA llm AUTHORIZATION llm_gateway;
SQL
