# ADR 0001 — Migrar a API de Flask para FastAPI

- **Status:** aceito, adiado para depois do primeiro deploy
- **Data:** 2026-08-31
- **Decisão de:** Davi + orientador

## Contexto

`app.py` é hoje uma app **Flask** com ~30 rotas: busca, ficha do filme, listas,
diário, essenciais, upload do `ratings.csv` do Letterboxd, autocomplete e as
rotas de streaming. Os request/response são validados **à mão** (`data.get(...)`,
`try/except int(...)`, checagens espalhadas) e **não há contrato publicado** —
nenhum OpenAPI, nenhum schema de resposta.

O orientador recomendou FastAPI. **O ganho não é velocidade** (o gargalo é o
modelo de embeddings, não o framework HTTP) — é:

1. **Contrato tipado com Pydantic** nos request e response: validação declarativa,
   erros 422 consistentes, menos código de parsing.
2. **OpenAPI automático** (`/docs`, `/openapi.json`): a API vira documentada e
   testável por contrato; clientes podem ser gerados.
3. `slowapi` (rate limit) e a instrumentação encaixam de forma mais limpa que os
   equivalentes Flask.

## Decisão

**Adiar a migração da API para depois do primeiro deploy.** Motivos:

- É a tarefa mais longa e arriscada da lista de produção (reescrever 30 rotas,
  upload multipart, `render_template`, tratamento de erro) — risco de regressão
  alto perto do deploy.
- O valor é de **narrativa de engenharia / manutenção**, não de desbloqueio: a
  API atual funciona e já ganhou rate limit (Flask-Limiter) e métricas.

**O que já foi feito para facilitar a migração:**

- O **serviço de inferência** (`inference/main.py`) **já é FastAPI**, com Pydantic
  e OpenAPI — a parte pesada (ML) já está no modelo-alvo.
- A costura `core/inference_client.py` isola as chamadas de ML; a API só as
  invoca por função, sem acoplar ao framework.
- Rate limit e métricas ficaram atrás de wrappers (`limiter.limit`,
  `core.metrics.init_flask`) que têm equivalente direto em FastAPI/Starlette
  (`slowapi`, middleware ASGI + `prometheus_client.make_asgi_app`).

## Plano da migração (quando for feita)

1. Subir uma app FastAPI paralela (`api/main.py`), montando o frontend estático
   com `StaticFiles` e o `index.html` via `Jinja2Templates`.
2. Migrar rota a rota, de baixo risco para alto:
   `/health`, `/genres`, `/people`, `/popular` → `/search` → ficha e streaming →
   listas/diário/versões → uploads (`/recommend`, `/submit_ratings`).
   Para cada rota: um `BaseModel` de request e um de response.
3. Trocar Flask-Limiter por **slowapi** (`@limiter.limit` no router).
4. Trocar `core.metrics.init_flask` por um middleware ASGI + `make_asgi_app()`
   em `/metrics` (o serviço de inferência já faz assim).
5. `gunicorn -k uvicorn.workers.UvicornWorker api.main:app` no `docker-compose`.
6. Contrato: publicar o `openapi.json` como artefato de CI e versioná-lo para
   pegar quebras de contrato em PR.

## Consequências

- **Positivas:** contrato explícito e versionado, menos código de validação,
  `/docs` interativo, caminho aberto para clientes gerados.
- **Negativas / custo:** ~1–2 dias de trabalho + risco de regressão; duas libs
  de web no requirements durante a transição (Flask e FastAPI coexistem enquanto
  a migração é incremental).
- **Sem impacto de performance** esperado (o framework não é o gargalo).
