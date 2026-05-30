CREATE DATABASE IF NOT EXISTS adpilot;

CREATE TABLE IF NOT EXISTS adpilot.ad_performance_merged (
    tenant_id  UUID,
    ad_id      String,
    date       Date,
    spend      Float64,
    impressions UInt32,
    clicks     UInt32,
    conversions UInt32,
    revenue    Float64,
    source     String
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, date, ad_id)
TTL date + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
