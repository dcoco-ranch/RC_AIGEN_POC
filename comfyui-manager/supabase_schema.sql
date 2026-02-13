-- =============================================
-- ComfyUI Manager - Supabase Schema
-- Run this in your Supabase SQL Editor
-- =============================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================
-- Users Table
-- =============================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    is_admin BOOLEAN DEFAULT FALSE,
    gitlab_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for email lookups
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_gitlab_id ON users(gitlab_id);

-- =============================================
-- Jobs Table
-- =============================================
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    type TEXT NOT NULL CHECK (type IN ('IMAGE_TASK', 'VIDEO_TASK')),
    cost_rcc INTEGER NOT NULL,
    status TEXT DEFAULT 'created' CHECK (status IN ('created', 'running', 'succeeded', 'failed')),
    duration_ms INTEGER,
    output_uri TEXT,
    metadata JSONB,
    admin_bypass BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

-- Indexes for jobs
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);

-- =============================================
-- RCC Ledger Table (CRITICAL - Source of Truth)
-- =============================================
CREATE TABLE IF NOT EXISTS rcc_ledger (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN (
        'JOB_RESERVE',
        'JOB_RELEASE',
        'SUBSCRIPTION_GRANT',
        'TOPUP_GRANT',
        'MANUAL_ADJUST',
        'ADMIN_BYPASS'
    )),
    job_id BIGINT REFERENCES jobs(id),
    external_ref TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for ledger
CREATE INDEX IF NOT EXISTS idx_rcc_ledger_user_id ON rcc_ledger(user_id);
CREATE INDEX IF NOT EXISTS idx_rcc_ledger_reason ON rcc_ledger(reason);
CREATE INDEX IF NOT EXISTS idx_rcc_ledger_created_at ON rcc_ledger(created_at);

-- =============================================
-- Payments Table (Audit)
-- =============================================
CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    provider TEXT DEFAULT 'stripe',
    type TEXT NOT NULL CHECK (type IN ('subscription', 'topup')),
    amount INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed', 'refunded')),
    external_ref TEXT,
    stripe_event_id TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for payments
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_external_ref ON payments(external_ref);
CREATE INDEX IF NOT EXISTS idx_payments_stripe_event_id ON payments(stripe_event_id);

-- =============================================
-- Logs Table (Ops & Audit)
-- =============================================
CREATE TABLE IF NOT EXISTS logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    ip TEXT,
    action TEXT NOT NULL,
    details TEXT,
    status TEXT DEFAULT 'success',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for logs
CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_action ON logs(action);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);

-- =============================================
-- App Settings Table (Key-Value Store)
-- =============================================
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Insert default settings
INSERT INTO app_settings (key, value) VALUES ('comfyui_public_port', '8188')
ON CONFLICT (key) DO NOTHING;

-- =============================================
-- Views for Common Queries
-- =============================================

-- User balance view (calculated from ledger)
CREATE OR REPLACE VIEW user_balances AS
SELECT 
    u.id as user_id,
    u.email,
    u.is_admin,
    COALESCE(SUM(l.delta), 0) as rcc_balance
FROM users u
LEFT JOIN rcc_ledger l ON u.id = l.user_id
GROUP BY u.id, u.email, u.is_admin;

-- Daily stats view
CREATE OR REPLACE VIEW daily_stats AS
SELECT 
    DATE(created_at) as date,
    COUNT(*) as total_jobs,
    COUNT(*) FILTER (WHERE status = 'succeeded') as succeeded_jobs,
    COUNT(*) FILTER (WHERE status = 'failed') as failed_jobs,
    SUM(cost_rcc) FILTER (WHERE status = 'succeeded' AND NOT admin_bypass) as rcc_consumed
FROM jobs
GROUP BY DATE(created_at)
ORDER BY date DESC;

-- =============================================
-- Row Level Security (RLS) - Optional
-- =============================================

-- Enable RLS on tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE rcc_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs ENABLE ROW LEVEL SECURITY;

-- Policies will depend on your authentication setup
-- These are examples for reference:

-- Users can read their own data
-- CREATE POLICY "Users can read own data" ON users
--     FOR SELECT USING (auth.uid()::text = id::text);

-- Jobs belong to users
-- CREATE POLICY "Users can read own jobs" ON jobs
--     FOR SELECT USING (auth.uid()::text = user_id::text);

-- =============================================
-- Functions
-- =============================================

-- Function to calculate user balance
CREATE OR REPLACE FUNCTION get_user_balance(p_user_id BIGINT)
RETURNS INTEGER AS $$
BEGIN
    RETURN COALESCE(
        (SELECT SUM(delta) FROM rcc_ledger WHERE user_id = p_user_id),
        0
    );
END;
$$ LANGUAGE plpgsql;

-- Function to check if user has sufficient balance
CREATE OR REPLACE FUNCTION has_sufficient_balance(p_user_id BIGINT, p_required INTEGER)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN get_user_balance(p_user_id) >= p_required;
END;
$$ LANGUAGE plpgsql;

-- =============================================
-- Sample Data (Optional - for testing)
-- =============================================

-- Create a test admin user (password: admin123)
-- INSERT INTO users (email, password_hash, is_admin)
-- VALUES ('admin@ranchcomputing.com', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKxcQw0FJnNuXXu', true);

-- Grant initial RCC to test user
-- INSERT INTO rcc_ledger (user_id, delta, reason, external_ref)
-- VALUES (1, 100, 'MANUAL_ADJUST', 'initial_test_credit');
