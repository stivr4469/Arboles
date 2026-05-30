-- Facebook Ad Accounts (до 10 на тенанта)
CREATE TABLE ad_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    fb_act_id VARCHAR(100) NOT NULL,          -- act_XXXXXXXXX
    encrypted_token BYTEA NOT NULL,           -- AES-256-GCM encrypted System User Token
    token_iv BYTEA NOT NULL,                  -- IV для AES
    token_tag BYTEA NOT NULL,                 -- GCM auth tag
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, fb_act_id)
);

CREATE INDEX idx_ad_accounts_tenant_id ON ad_accounts(tenant_id);

-- Keitaro Tracker Configs (один на тенанта на старте)
CREATE TABLE keitaro_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    base_url VARCHAR(500) NOT NULL,           -- https://tracker.example.com
    encrypted_api_key BYTEA NOT NULL,
    api_key_iv BYTEA NOT NULL,
    api_key_tag BYTEA NOT NULL,
    ad_id_param VARCHAR(20) NOT NULL DEFAULT 'sub1',  -- sub1/sub2/sub3
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id)
);

CREATE INDEX idx_keitaro_configs_tenant_id ON keitaro_configs(tenant_id);
