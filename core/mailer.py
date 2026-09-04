# -*- coding: utf-8 -*-
"""Envio de e-mail transacional (verificação de conta, reset de senha).

Configuração por ambiente:
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_STARTTLS=1

Sem `SMTP_HOST`, cai no **modo console**: imprime o e-mail (com o link) no stdout
— dá para testar o fluxo inteiro sem provedor. Nunca lança: falha de envio vira
`False` + log.
"""

from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage

APP_NAME = os.environ.get("RECOMENDAI_APP_NAME", "Cinerd")


def _cfg() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from": os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "no-reply@recomendai.local")),
        "starttls": os.environ.get("SMTP_STARTTLS", "1").lower() not in ("0", "false", "no"),
    }


def is_configured() -> bool:
    return bool(_cfg()["host"])


def send(to: str, subject: str, body_text: str) -> bool:
    """Envia (ou, sem SMTP, imprime) um e-mail de texto. Devolve True se ok."""
    c = _cfg()
    if not c["host"]:
        print(
            "\n"
            + "=" * 62
            + f"\n[e-mail:console] para: {to}\nassunto: {subject}\n"
            + "-" * 62
            + f"\n{body_text}\n"
            + "=" * 62
            + "\n",
            file=sys.stderr,
        )
        return True
    msg = EmailMessage()
    msg["From"] = c["from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)
    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=15) as s:
            if c["starttls"]:
                s.starttls()
            if c["user"]:
                s.login(c["user"], c["password"])
            s.send_message(msg)
        return True
    except Exception as e:  # nunca derruba o fluxo de auth por causa do e-mail
        print(f"[mailer] falha ao enviar para {to}: {e}", file=sys.stderr)
        return False


def send_verify(to: str, link: str) -> bool:
    return send(
        to,
        f"{APP_NAME} — confirme seu e-mail",
        f"Bem-vindo ao {APP_NAME}!\n\nConfirme seu e-mail abrindo:\n{link}\n\n"
        "O link vale por 24 horas. Se não foi você, ignore esta mensagem.",
    )


def send_reset(to: str, link: str) -> bool:
    return send(
        to,
        f"{APP_NAME} — redefinição de senha",
        f"Recebemos um pedido para redefinir sua senha no {APP_NAME}.\n\n"
        f"Abra para escolher uma nova senha:\n{link}\n\n"
        "O link vale por 24 horas. Se não foi você, ignore — sua senha continua a mesma.",
    )
