-- 036_emp_insurance_is_deleted.sql
-- employee_insurance_overrides에 is_deleted 추가 (query가 필터로 참조).
-- 자동생성 스키마(034)엔 total 빈테이블이라 컬럼 미포함이었음.
ALTER TABLE employee_insurance_overrides ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
