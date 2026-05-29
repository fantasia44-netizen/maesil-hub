"""email_service.py — Resend API 기반 이메일 발송.

설정 (saas_config 우선, env 폴백):
    resend_api_key  — Resend API Key
    resend_from_email — 발신 주소 (예: 매실 허브 <noreply@maesil-hub.com>)

미설정 시 이메일을 보내지 않고 로그에만 링크를 출력 (개발 환경 친화적).
"""
from __future__ import annotations

import logging
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)


def _get_resend_config() -> tuple[str, str]:
    """(api_key, from_email) 반환. saas_config → env 순으로 조회."""
    try:
        from services.saas_config import get_config
        api_key = get_config('resend_api_key') or ''
        from_email = get_config('resend_from_email') or ''
    except Exception:
        api_key = ''
        from_email = ''

    if not api_key:
        import os
        api_key = os.environ.get('RESEND_API_KEY', '')
    if not from_email:
        import os
        from_email = os.environ.get('RESEND_FROM_EMAIL', '매실 허브 <noreply@maesil-insight.com>')

    return api_key.strip(), from_email.strip()


def send_email(to: str, subject: str, html: str) -> bool:
    """단순 이메일 발송. 성공 시 True, 실패 시 False."""
    api_key, from_email = _get_resend_config()

    if not api_key:
        # 개발 환경: 로그만 출력
        logger.warning(
            f'[EmailService] resend_api_key 미설정 — 이메일 미발송\n'
            f'  To: {to}\n  Subject: {subject}'
        )
        return False

    payload = json.dumps({
        'from': from_email,
        'to': [to],
        'subject': subject,
        'html': html,
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            logger.info(f'[EmailService] 발송 성공 → {to} ({resp.status})')
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'[EmailService] Resend HTTP {e.code}: {body[:300]}')
        return False
    except Exception as e:
        logger.error(f'[EmailService] 발송 실패: {e}')
        return False


def send_invite_email(to_email: str, biz_name: str,
                      inviter_name: str, role_label: str,
                      join_url: str) -> bool:
    """팀 초대 이메일 발송."""
    subject = f'[매실 허브] {biz_name}에서 팀 초대가 도착했습니다'
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family:Apple SD Gothic Neo,Malgun Gothic,sans-serif;background:#f8f9fa;margin:0;padding:32px;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
              padding:40px;box-shadow:0 2px 12px rgba(0,0,0,.08);">
    <div style="text-align:center;margin-bottom:28px;">
      <span style="font-size:32px;">🍑</span>
      <h2 style="color:#2d6a4f;margin:8px 0 0;">매실 허브</h2>
    </div>
    <h3 style="color:#1a1a1a;margin-bottom:16px;">팀 초대</h3>
    <p style="color:#444;line-height:1.7;">
      <strong>{inviter_name}</strong>님이 <strong>{biz_name}</strong>의
      팀원으로 초대했습니다.<br>
      역할: <span style="background:#e8f5e9;color:#2d6a4f;padding:2px 8px;border-radius:4px;font-weight:600;">{role_label}</span>
    </p>
    <div style="text-align:center;margin:32px 0;">
      <a href="{join_url}"
         style="background:#2d6a4f;color:#fff;padding:14px 36px;border-radius:8px;
                text-decoration:none;font-weight:700;font-size:16px;display:inline-block;">
        초대 수락하기
      </a>
    </div>
    <p style="color:#888;font-size:13px;text-align:center;margin-top:24px;">
      이 링크는 7일간 유효합니다.<br>
      초대를 원하지 않으시면 이 이메일을 무시하세요.
    </p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
    <p style="color:#aaa;font-size:11px;text-align:center;">
      매실 허브 — 식품·축산 ERP/WMS SaaS
    </p>
  </div>
</body>
</html>"""
    return send_email(to_email, subject, html)


def send_password_reset_email(to_email: str, name: str, reset_url: str) -> bool:
    """비밀번호 재설정 이메일 발송."""
    subject = '[매실 허브] 비밀번호 재설정 안내'
    greeting = f'<strong>{name}</strong>님, ' if name else ''
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family:Apple SD Gothic Neo,Malgun Gothic,sans-serif;background:#f8f9fa;margin:0;padding:32px;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
              padding:40px;box-shadow:0 2px 12px rgba(0,0,0,.08);">
    <div style="text-align:center;margin-bottom:28px;">
      <span style="font-size:32px;">🍑</span>
      <h2 style="color:#2d6a4f;margin:8px 0 0;">매실 허브</h2>
    </div>
    <h3 style="color:#1a1a1a;margin-bottom:16px;">비밀번호 재설정</h3>
    <p style="color:#444;line-height:1.7;">
      {greeting}비밀번호 재설정을 요청하셨습니다.<br>
      아래 버튼을 클릭해 새 비밀번호를 설정해 주세요.
    </p>
    <div style="text-align:center;margin:32px 0;">
      <a href="{reset_url}"
         style="background:#2d6a4f;color:#fff;padding:14px 36px;border-radius:8px;
                text-decoration:none;font-weight:700;font-size:16px;display:inline-block;">
        비밀번호 재설정
      </a>
    </div>
    <p style="color:#888;font-size:13px;text-align:center;margin-top:24px;">
      이 링크는 <strong>1시간</strong> 후 만료됩니다.<br>
      본인이 요청하지 않으셨다면 이 이메일을 무시해 주세요.
    </p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
    <p style="color:#aaa;font-size:11px;text-align:center;">
      매실 허브 — 식품·축산 ERP/WMS SaaS
    </p>
  </div>
</body>
</html>"""
    return send_email(to_email, subject, html)
