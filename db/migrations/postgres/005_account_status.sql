-- Track last known Meta account health (updated hourly by collect_fb_task)
ALTER TABLE ad_accounts
    ADD COLUMN IF NOT EXISTS last_status_code SMALLINT,        -- Meta account_status: 1=ACTIVE, 2=DISABLED, 3=UNSETTLED, 7=PENDING_RISK_REVIEW, 101=CLOSED
    ADD COLUMN IF NOT EXISTS last_disable_reason SMALLINT,     -- Meta disable_reason code, 0 = none
    ADD COLUMN IF NOT EXISTS status_checked_at TIMESTAMPTZ;    -- timestamp of last successful check
