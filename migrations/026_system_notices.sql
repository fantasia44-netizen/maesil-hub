-- 026_system_notices.sql
-- 전역 시스템 공지/점검 브로드캐스트 (슈퍼어드민 → 전체 사용자 배너).
-- biz_id 없음 = 플랫폼 전역 공지 (insight system_notices 패턴).
-- 표시 조건: is_active AND now() BETWEEN starts_at AND ends_at (null=무제한).

CREATE TABLE IF NOT EXISTS system_notices (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'info',   -- info | warning | danger | success
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    starts_at   TIMESTAMPTZ,
    ends_at     TIMESTAMPTZ,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  TEXT DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 활성 공지 조회 최적화 (배너용 — 매 요청 조회 가능성)
CREATE INDEX IF NOT EXISTS idx_system_notices_active
    ON system_notices (is_active, starts_at, ends_at)
    WHERE is_active = TRUE;

-- RLS: 서비스롤 전체 + 인증사용자 SELECT (전역 공지이므로 테넌트 격리 없음)
ALTER TABLE system_notices ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_role_all ON system_notices;
CREATE POLICY service_role_all ON system_notices
    FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

DROP POLICY IF EXISTS authenticated_read ON system_notices;
CREATE POLICY authenticated_read ON system_notices
    FOR SELECT TO authenticated USING (TRUE);
