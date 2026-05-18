-- 007: invitations — 업체 멤버 이메일 초대 토큰 관리

CREATE TABLE IF NOT EXISTS invitations (
    id           BIGSERIAL PRIMARY KEY,
    biz_id       BIGINT      NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    email        TEXT        NOT NULL,
    role         TEXT        NOT NULL DEFAULT 'viewer',
    token        TEXT        NOT NULL UNIQUE,
    invited_by   BIGINT      REFERENCES app_users(id) ON DELETE SET NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    used_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invitations_token    ON invitations(token);
CREATE INDEX IF NOT EXISTS idx_invitations_biz      ON invitations(biz_id);
CREATE INDEX IF NOT EXISTS idx_invitations_email    ON invitations(email);
-- 동일 업체·이메일 중복 초대 방지 (미사용 상태만)
CREATE UNIQUE INDEX IF NOT EXISTS uq_invitations_biz_email_active
    ON invitations(biz_id, email)
    WHERE used_at IS NULL;

COMMENT ON TABLE invitations IS '업체 멤버 초대 토큰. 7일 유효, used_at 세팅 시 소진.';
