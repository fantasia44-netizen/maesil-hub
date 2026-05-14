"""슈퍼어드민 계정 시드 (Phase 0).
사용법: python scripts/seed_admin.py <email> <password>
"""
import os
import sys
import bcrypt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')


def main():
    if len(sys.argv) < 3:
        print('Usage: python scripts/seed_admin.py <email> <password>')
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    if len(password) < 10:
        print('[ERROR] password must be at least 10 chars')
        sys.exit(1)

    from db.client import get_admin_client
    c = get_admin_client()

    # 이미 있나?
    existing = c.table('app_users').select('id, is_super_admin') \
        .eq('email', email).execute().data
    if existing:
        u = existing[0]
        if u['is_super_admin']:
            print(f'[OK] already super admin: {email} (id={u["id"]})')
            return
        c.table('app_users').update({'is_super_admin': True}) \
            .eq('id', u['id']).execute()
        print(f'[OK] promoted to super admin: {email} (id={u["id"]})')
        return

    pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode()
    res = c.table('app_users').insert({
        'email': email,
        'password_hash': pw_hash,
        'name': 'Super Admin',
        'is_super_admin': True,
        'email_verified': True,
    }).execute()
    print(f'[OK] super admin created: {email} (id={res.data[0]["id"]})')


if __name__ == '__main__':
    main()
