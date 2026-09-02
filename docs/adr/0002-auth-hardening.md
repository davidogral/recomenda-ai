# ADR 0002 — Endurecimento da autenticação própria

- **Status:** aceito, implementado
- **Data:** 2026-09-02
- **Decisão de:** Davi + orientador

## Contexto

O commit `c608153` adicionou contas de usuário (e-mail + senha) para persistir
diário e listas por conta. A revisão do orientador levantou o custo de operar
autenticação própria num site público brasileiro que coleta e-mail, senha e
histórico de consumo cultural, com uma checklist: hash lento, rate limit em
login/recuperação, recuperação sem enumeração, cookie `HttpOnly`/`Secure`/
`SameSite=Lax`, e caminho de exclusão de conta por LGPD.

Auditoria do que já existia: Argon2id ✅, rate limit nas rotas `/auth/*` ✅,
`/auth/forgot` sem enumeração ✅, CSRF em toda rota que muda estado ✅, exclusão
de conta com re-autenticação ✅. Buracos reais encontrados:

1. **`SECRET_KEY` com fallback duplicado e adivinhável.** `auth_routes` caía em
   `secrets.token_hex(32)` (aleatório por boot); `users._serializer` caía, de
   forma independente, em `"dev-insecure-" + pid`. Sem `SECRET_KEY` em produção,
   um token de reset ficava **forjável** (PID tem espaço pequeno).
2. **Cookie de sessão sem `Secure` por padrão** (`RECOMENDAI_COOKIE_SECURE=0`) e
   **cookie *remember* do Flask-Login sem `Secure` nem `SameSite`**.
3. **Token de reset reutilizável** dentro da janela de 24 h (sem uso único).
4. **Canal lateral de timing no login**: `authenticate()` retornava antes de
   rodar Argon2 quando o e-mail não existia → dava para distinguir "sem conta"
   de "senha errada".
5. `email_verified` era rastreado mas não barrava nada.

## Decisão

**Manter autenticação própria e fechar os buracos** — em vez de trocar por
Google OAuth (a alternativa sugerida). Motivo: o custo do OAuth (dependência de
provedor, fluxo de callback, ainda precisar de política de dados) não compensa
para o volume atual, e os itens da checklist são pontuais. Reavaliar se surgir
necessidade de SSO.

### O que mudou

| Buraco | Correção |
|---|---|
| 1 | `core/security.py` — fonte **única** do segredo. `RECOMENDAI_ENV=production` sem `SECRET_KEY` → `MissingSecretKey` no boot (não sobe). Dev persiste a chave em `data/.secret_key` (`0600`, fora do git). |
| 2 | `init_auth` força `SESSION_COOKIE_*` **e** `REMEMBER_COOKIE_*` com `HttpOnly` + `SameSite=Lax`; `Secure` liga sozinho em produção (`RECOMENDAI_COOKIE_SECURE` força/desliga). Duração do *remember* 365d → 30d; cadastro entra só com cookie de sessão. |
| 3 | Token de reset/verificação carrega um fingerprint do estado mutável da conta (`_token_fingerprint`): hash da senha (reset) / flag `email_verified` (verify). Usar o link muda esse estado → o link morre. Uso único na prática. |
| 4 | `authenticate()` roda um `verify` contra um hash-dummy quando o e-mail não existe — iguala o tempo de resposta. |
| 5 | `@verified_required` nas rotas que **gravam** dado pessoal (`/ratings`, `/lists*`, `/versions`), **só quando `mailer.is_configured()`** — sem SMTP não dá para exigir verificação. Leitura e exclusão seguem livres. |
| — | `ProxyFix(x_for=1, x_proto=1, x_host=1)` em `app.py` quando `RECOMENDAI_ENV=production` — o Flask enxerga `https`/host reais atrás do Caddy; sem isso os links de verificação/reset saíam com esquema `http`. |

### Rate limit sob multi-worker

O `Flask-Limiter` usa storage em memória — o limite vale **por processo**. Com
`gunicorn -w 2` o teto efetivo dobrava. Não há Redis no alvo de deploy (VM
free-tier). **Decisão:** rodar a API com `gunicorn -w 1 --threads 8 -k gthread`
(o trabalho pesado de ML está no serviço `inference` à parte, então o throughput
real não muda) e deixar `RATELIMIT_STORAGE_URI` como override para quando houver
Redis. `init_auth` loga um aviso se o storage for `memory://`.

## Fora de escopo (próximo passo)

- **LGPD além da exclusão**: página de política de privacidade + endpoint de
  exportação de dados (`GET /auth/export`, direito de acesso/portabilidade).
- **Enumeração no `/register`**: mantém `409` (risco baixo — `/forgot` já é
  seguro, rate limit 5/h). Zerar exigiria fluxo double-opt-in (muda a UX do
  cadastro).

## Consequências

- **Positivas:** checklist do orientador fechada; segredo com um caminho só e
  fatal quando falta; links de e-mail de uso único; menos superfície de timing.
  Subseção "Contas & segurança" no README vira sinal de portfólio (o threat
  model foi considerado).
- **Custo operacional:** o deploy precisa setar `SECRET_KEY`, `RECOMENDAI_ENV=production`
  e `RECOMENDAI_COOKIE_SECURE=1`, e ajustar o `ExecStart` do gunicorn para
  `-w 1`. Rotacionar o `SECRET_KEY` uma vez desloga todas as sessões e invalida
  links de reset pendentes (aceitável).
- **Sem impacto de latência.**
