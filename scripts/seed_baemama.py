"""배마마 회사 + admin@maesil.net을 owner로 시드.

테스트 환경 구축의 첫 단계.
실행: python scripts/seed_baemama.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

from db.client import get_admin_client


def main():
    c = get_admin_client()

    # 1) Starter 플랜 ID 가져오기
    plan = c.table('plans').select('id').eq('code', 'starter').single().execute().data
    starter_id = plan['id']

    # 2) 배마마 회사 (없으면 생성)
    existing = c.table('businesses').select('id').eq('name', '배마마').execute().data
    if existing:
        biz_id = existing[0]['id']
        print(f'[OK] business already exists: 배마마 (id={biz_id})')
    else:
        res = c.table('businesses').insert({
            'name': '배마마',
            'biz_reg_no': '830-45-01231',
            'representative': '관리자',
            'industry': 'food',
            'plan_id': starter_id,
            'status': 'active',
        }).execute()
        biz_id = res.data[0]['id']
        print(f'[OK] business created: 배마마 (id={biz_id})')

    # 3) admin@maesil.net 사용자 ID
    admin = c.table('app_users').select('id').eq('email', 'admin@maesil.net').single().execute().data
    if not admin:
        print('[ERROR] admin@maesil.net not found. Run seed_admin.py first.')
        sys.exit(1)
    admin_id = admin['id']

    # 4) user_business_map에 owner로 등록
    existing_map = c.table('user_business_map').select('id').eq('user_id', admin_id).eq('biz_id', biz_id).execute().data
    if existing_map:
        print(f'[OK] mapping already exists: user={admin_id} biz={biz_id}')
    else:
        c.table('user_business_map').insert({
            'user_id': admin_id,
            'biz_id': biz_id,
            'role': 'owner',
            'is_primary': True,
        }).execute()
        print(f'[OK] mapped admin -> 배마마 (owner, primary)')

    # 5) 구독 (trial 14일)
    existing_sub = c.table('subscriptions').select('id').eq('biz_id', biz_id).execute().data
    if not existing_sub:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        c.table('subscriptions').insert({
            'biz_id': biz_id,
            'plan_id': starter_id,
            'status': 'trial',
            'current_period_start': now.isoformat(),
            'current_period_end': (now + timedelta(days=14)).isoformat(),
        }).execute()
        print(f'[OK] subscription created: trial 14d')

    # 6) my_business 등록 (거래명세서 발행 정보)
    existing_mb = c.table('my_business').select('id').eq('biz_id', biz_id).execute().data
    if not existing_mb:
        c.table('my_business').insert({
            'biz_id': biz_id,
            'name': '배마마',
            'biz_reg_no': '830-45-01231',
            'representative': '관리자',
            'is_default': True,
        }).execute()
        print(f'[OK] my_business created')

    print()
    print('=' * 60)
    print(f'Seed complete: biz_id={biz_id} (배마마)')
    print(f'admin@maesil.net is now owner of 배마마')
    print('=' * 60)


if __name__ == '__main__':
    main()
