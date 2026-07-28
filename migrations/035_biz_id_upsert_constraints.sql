-- 035_biz_id_upsert_constraints.sql
-- biz_id 주입 누락 insert/upsert 12종 수정(db_supabase.py)에 수반되는 스키마.
-- upsert on_conflict을 (biz_id,...) 복합으로 바꿈에 따라 복합 UNIQUE 필요.
-- platform_settlements는 on_conflict가 참조하는 api_reference 컬럼도 추가.

ALTER TABLE platform_settlements ADD COLUMN IF NOT EXISTS api_reference TEXT;

ALTER TABLE codef_connections     ADD CONSTRAINT uq_codef_biz_conn   UNIQUE (biz_id, connected_id);
ALTER TABLE platform_settlements  ADD CONSTRAINT uq_platsettle_biz   UNIQUE (biz_id, channel, settlement_date, api_reference);
ALTER TABLE role_permissions      ADD CONSTRAINT uq_roleperm_biz     UNIQUE (biz_id, role, page_key);
ALTER TABLE insurance_rates       ADD CONSTRAINT uq_insrate_biz      UNIQUE (biz_id, year, insurance_type);
