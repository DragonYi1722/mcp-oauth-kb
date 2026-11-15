-- Schema for the knowledge base. Applied automatically by the postgres image.
-- Seed users/documents are created by scripts/bootstrap_seed.py after Keycloak
-- has assigned the real `sub` values.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS departments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,              -- Keycloak sub (issuer, sub) is the identity
    username       TEXT NOT NULL UNIQUE,
    email          TEXT,
    department_id  UUID REFERENCES departments(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'visibility') THEN
        CREATE TYPE visibility AS ENUM ('private', 'department', 'org');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'doc_status') THEN
        CREATE TYPE doc_status AS ENUM ('active', 'archived');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'principal_type') THEN
        CREATE TYPE principal_type AS ENUM ('user', 'department', 'role');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'permission') THEN
        CREATE TYPE permission AS ENUM ('read', 'write', 'admin');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS documents (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    owner_id       TEXT NOT NULL REFERENCES users(id),
    department_id  UUID REFERENCES departments(id),
    visibility     visibility NOT NULL DEFAULT 'department',
    status         doc_status NOT NULL DEFAULT 'active',
    version        INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_owner_idx ON documents(owner_id);
CREATE INDEX IF NOT EXISTS documents_dept_idx ON documents(department_id);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents(status);
CREATE INDEX IF NOT EXISTS documents_title_trgm_idx
    ON documents USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS documents_content_trgm_idx
    ON documents USING GIN (content gin_trgm_ops);

CREATE TABLE IF NOT EXISTS document_acl (
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    principal_type  principal_type NOT NULL,
    principal_id    TEXT NOT NULL,
    permission      permission NOT NULL,
    PRIMARY KEY (document_id, principal_type, principal_id, permission)
);

CREATE INDEX IF NOT EXISTS document_acl_principal_idx
    ON document_acl(principal_type, principal_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id               BIGSERIAL PRIMARY KEY,
    request_id       TEXT NOT NULL,
    actor_sub        TEXT NOT NULL,
    actor_username   TEXT,
    tool_name        TEXT NOT NULL,
    args_digest      TEXT NOT NULL,
    result           TEXT NOT NULL CHECK (result IN ('ok', 'denied', 'error')),
    error_code       TEXT,
    duration_ms      INTEGER NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_logs_actor_idx
    ON audit_logs(actor_sub, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_logs_created_idx
    ON audit_logs(created_at DESC);

INSERT INTO departments (id, name) VALUES
    ('11111111-1111-1111-1111-111111111111', 'engineering'),
    ('22222222-2222-2222-2222-222222222222', 'sales'),
    ('33333333-3333-3333-3333-333333333333', 'it')
ON CONFLICT (name) DO NOTHING;
