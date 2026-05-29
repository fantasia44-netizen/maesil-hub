-- 020_password_reset_tokens.sql
-- 비밀번호 찾기 토큰 테이블

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    token       TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prt_token ON password_reset_tokens(token);
CREATE INDEX IF NOT EXISTS idx_prt_email ON password_reset_tokens(email);

-- 만료된 토큰 자동 정리 (선택)
-- 24시간 후 만료, 별도 cron 없이 쿼리 시 필터링으로 처리
