-- Creative Entities для Intelligence Engine
CREATE TABLE creative_entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    creative_hash VARCHAR(64) NOT NULL,       -- SHA256 of creative content
    ad_id VARCHAR(100),                       -- Facebook ad_id если привязан
    dna JSONB NOT NULL DEFAULT '{}',          -- {observable, psychological, persuasion}
    embedding vector(1536),                   -- pgvector для семантического поиска
    performance_score FLOAT,                  -- CCS метрика
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, creative_hash)
);

CREATE INDEX idx_creative_entities_tenant_id ON creative_entities(tenant_id);
CREATE INDEX idx_creative_entities_embedding ON creative_entities USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Entity Relations (граф связей)
CREATE TABLE entity_relations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    from_entity_id UUID NOT NULL REFERENCES creative_entities(id) ON DELETE CASCADE,
    to_entity_id UUID NOT NULL REFERENCES creative_entities(id) ON DELETE CASCADE,
    relation_type VARCHAR(50) NOT NULL,       -- geo, vertical, trigger, framework
    weight FLOAT NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_entity_relations_tenant_id ON entity_relations(tenant_id);
CREATE INDEX idx_entity_relations_from ON entity_relations(from_entity_id);
