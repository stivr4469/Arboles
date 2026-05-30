-- RLS на всех таблицах с tenant_id
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE ad_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE keitaro_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE creative_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_relations ENABLE ROW LEVEL SECURITY;

-- Политика: видишь только своих
CREATE POLICY tenant_isolation_users ON users
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_ad_accounts ON ad_accounts
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_keitaro_configs ON keitaro_configs
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_creative_entities ON creative_entities
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_entity_relations ON entity_relations
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Суперпользователь (сервисная роль) обходит RLS
-- Создаём роль приложения
CREATE ROLE adpilot_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO adpilot_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO adpilot_app;
