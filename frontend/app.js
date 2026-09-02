/* Cinerd — front-end. Extraído do <script> inline de index.html sem alterar
   comportamento; servido como /static/app.js (cacheável) e carregado com defer.
   Blocos novos adicionados abaixo: navegação agrupada (goTo), skeleton loaders,
   e o tour de coach-marks. */
'use strict';

        // ================= AUTENTICAÇÃO (sessão + CSRF) =================
        // Envolve window.fetch: manda o cookie de sessão e o header X-CSRFToken
        // em toda requisição que altera estado (POST/PUT/PATCH/DELETE).
        (function () {
            const _fetch = window.fetch.bind(window);
            let _csrf = null;
            async function token() {
                if (_csrf) return _csrf;
                try { _csrf = (await (await _fetch('/auth/csrf', { credentials: 'same-origin' })).json()).csrf_token; }
                catch (e) { _csrf = null; }
                return _csrf;
            }
            window.__csrfReset = () => { _csrf = null; };
            window.fetch = async function (input, init) {
                init = Object.assign({ credentials: 'same-origin' }, init || {});
                const method = (init.method || 'GET').toUpperCase();
                if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
                    const t = await token();
                    if (t) init.headers = Object.assign({}, init.headers, { 'X-CSRFToken': t });
                }
                return _fetch(input, init);
            };
        })();

        const AUTH = {
            user: null,
            async refresh() {
                try { this.user = (await (await fetch('/auth/me')).json()).user; }
                catch (e) { this.user = null; }
                this.render();
                return this.user;
            },
            render() {
                const bar = document.getElementById('authBar');
                if (this.user) {
                    bar.innerHTML = `<span class="auth-email" title="${this.user.email}">${this.user.email}</span>`
                        + (this.user.email_verified ? '' : ' <button class="auth-link" id="authResend">confirmar e-mail</button>')
                        + ' <button class="auth-link" id="authLogout">Sair</button>';
                    document.getElementById('authLogout').onclick = () => this.logout();
                    const rs = document.getElementById('authResend');
                    if (rs) rs.onclick = async () => { await fetch('/auth/resend-verification', { method: 'POST' }); rs.textContent = 'e-mail reenviado ✓'; };
                } else {
                    bar.innerHTML = '<button class="auth-link" id="authOpen">Entrar / Criar conta</button>';
                    document.getElementById('authOpen').onclick = () => this.open('login');
                }
                document.querySelectorAll('[data-needs-auth]').forEach(el => el.classList.toggle('locked', !this.user));
            },
            open(view) {
                document.getElementById('authModal').style.display = 'flex';
                this.show(view || 'login');
            },
            close() { document.getElementById('authModal').style.display = 'none'; this.msg(''); },
            show(view) {
                document.querySelectorAll('#authTabs button').forEach(b => b.classList.toggle('active', b.dataset.authtab === view));
                document.getElementById('authTabs').style.display = view === 'reset' ? 'none' : '';
                document.querySelectorAll('.auth-form').forEach(f => f.hidden = f.dataset.authview !== view);
                document.getElementById('authTitle').textContent =
                    ({ login: 'Entrar', register: 'Criar conta', forgot: 'Recuperar senha', reset: 'Nova senha' })[view];
                this.msg('');
            },
            msg(text, ok) {
                const m = document.getElementById('authMsg');
                m.textContent = text || ''; m.className = 'auth-msg' + (ok ? ' ok' : (text ? ' err' : ''));
            },
            async _post(url, body) {
                window.__csrfReset();
                const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
                let d = {}; try { d = await r.json(); } catch (e) { }
                return { ok: r.ok, status: r.status, d };
            },
            async login(email, password) {
                const { ok, d } = await this._post('/auth/login', { email, password });
                if (!ok) return this.msg(d.error || 'Não foi possível entrar.');
                this.close(); await this.refresh();
            },
            async register(email, password) {
                const { ok, d } = await this._post('/auth/register', { email, password });
                if (!ok) return this.msg(d.error || 'Não foi possível criar a conta.');
                this.close(); await this.refresh();
                if (d.needs_verification) this.msg('Conta criada! Confirme o e-mail pelo link que enviamos.', true);
            },
            async forgot(email) {
                const { d } = await this._post('/auth/forgot', { email });
                this.msg(d.message || 'Se o e-mail existir, enviamos um link.', true);
            },
            async reset(token, password) {
                const { ok, d } = await this._post('/auth/reset', { token, password });
                if (!ok) return this.msg(d.error || 'Não foi possível redefinir.');
                this.msg('Senha redefinida! Você já pode entrar.', true);
                this.show('login');
            },
            async logout() {
                await fetch('/auth/logout', { method: 'POST' });
                this.user = null; this.render(); location.href = '/';
            },
        };

        document.getElementById('authClose').onclick = () => AUTH.close();
        document.getElementById('authModal').addEventListener('click', e => { if (e.target.id === 'authModal') AUTH.close(); });
        document.querySelectorAll('#authTabs button').forEach(b => b.onclick = () => AUTH.show(b.dataset.authtab));
        document.getElementById('authFormLogin').addEventListener('submit', e => { e.preventDefault(); AUTH.login(e.target.email.value, e.target.password.value); });
        document.getElementById('authFormRegister').addEventListener('submit', e => { e.preventDefault(); AUTH.register(e.target.email.value, e.target.password.value); });
        document.getElementById('authFormForgot').addEventListener('submit', e => { e.preventDefault(); AUTH.forgot(e.target.email.value); });
        document.getElementById('authFormReset').addEventListener('submit', e => { e.preventDefault(); AUTH.reset(AUTH._resetToken, e.target.password.value); });

        function AUTH_TOAST(t) { const b = document.getElementById('authBar'); const s = document.createElement('span'); s.className = 'auth-toast'; s.textContent = t; b.appendChild(s); setTimeout(() => s.remove(), 6000); }

        AUTH.refresh().then(() => {
            const q = new URLSearchParams(location.search);
            if (q.get('verify') === 'ok') AUTH_TOAST('E-mail confirmado! ✓');
            else if (q.get('verify') === 'invalid') AUTH_TOAST('Link de confirmação inválido/expirado.');
            if (q.get('reset')) { AUTH._resetToken = q.get('reset'); AUTH.open('reset'); }
            if (q.toString()) history.replaceState({}, '', location.pathname);
        });

        // ---------- Navegação (Buscar + grupos Descobrir / Importar / Meu) ----------
        // Uma única função de troca de painel; a barra agrupa 7 destinos em 4 itens.
        const NAV = document.getElementById('nav');

        function closeNavMenus(except) {
            NAV.querySelectorAll('.nav-trigger').forEach(t => {
                if (t === except) return;
                t.setAttribute('aria-expanded', 'false');
                const m = document.getElementById(t.getAttribute('aria-controls'));
                if (m) m.hidden = true;
            });
        }

        function goTo(tab, opts) {
            opts = opts || {};
            const target = document.getElementById('tab-' + tab);
            if (!target) return;
            // Gate de login: destinos com data-needs-auth pedem conta.
            const needsAuth = NAV.querySelector(`[data-tab="${tab}"]`)?.hasAttribute('data-needs-auth');
            if (needsAuth && !AUTH.user) { closeNavMenus(); if (!opts.silent) AUTH.open('login'); return; }

            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            target.classList.add('active');

            // Estado visual da barra: item ativo + grupo pai destacado.
            NAV.querySelectorAll('.nav-item, .nav-sub').forEach(el => el.classList.remove('is-active'));
            const hit = NAV.querySelector(`[data-tab="${tab}"]`);
            if (hit) {
                hit.classList.add('is-active');
                const grp = hit.closest('.nav-group');
                if (grp) grp.querySelector('.nav-trigger').classList.add('is-active');
            }
            closeNavMenus();

            // Hooks de carregamento preguiçoso (idênticos ao comportamento anterior).
            if (tab === 'pick3' && !popularLoaded) { popularLoaded = true; loadPopular(); }
            if (tab === 'watched') loadWatched();
            if (tab === 'lists') showListsHome();
            if (tab === 'explore' && !exploreLoaded) { exploreLoaded = true; loadExploreOptions(); }

            if (!opts.noScroll && !opts.silent) {
                NAV.scrollIntoView({ block: 'start', behavior: 'smooth' });
            }
        }
        window.goTo = goTo;

        NAV.querySelectorAll('[data-tab]').forEach(btn => {
            btn.addEventListener('click', () => goTo(btn.dataset.tab));
        });
        NAV.querySelectorAll('.nav-trigger').forEach(trigger => {
            trigger.addEventListener('click', e => {
                e.stopPropagation();
                if (trigger.hasAttribute('data-needs-auth') && !AUTH.user) { AUTH.open('login'); return; }
                const menu = document.getElementById(trigger.getAttribute('aria-controls'));
                const open = trigger.getAttribute('aria-expanded') === 'true';
                closeNavMenus(open ? null : trigger);
                trigger.setAttribute('aria-expanded', String(!open));
                if (menu) {
                    menu.hidden = open;
                    // Vira o menu para a esquerda se estourar a borda direita da tela.
                    menu.classList.remove('flip');
                    if (!open && menu.getBoundingClientRect().right > innerWidth - 8) menu.classList.add('flip');
                }
            });
        });
        document.addEventListener('click', e => { if (!e.target.closest('.nav-group')) closeNavMenus(); });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeNavMenus(); });

        // ---------- Carregar gêneros (não crítico → ocioso) ----------
        function loadGenres() {
            fetch('/genres').then(r => r.json()).then(genres => {
                const sel = document.getElementById('filterGenre');
                genres.forEach(g => {
                    const o = document.createElement('option');
                    o.value = g; o.textContent = g; sel.appendChild(o);
                });
            }).catch(() => {});
        }

        // ---------- Utilitários ----------
        function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]); }
        const SIGNAL_CLASS = { keyword: 'sig-tema', synopsis: 'sig-sinopse', lexical: 'sig-termos', name: 'sig-nome' };

        // Modais empilháveis (ficha ↔ parecidos): o último aberto fica na frente.
        let modalZ = 1000;
        function bringToFront(overlay) { overlay.style.zIndex = ++modalZ; }

        function ratingBadge(movie) {
            if (movie.predicted_rating != null)
                return `<div class="movie-rating">⭐ ${movie.predicted_rating.toFixed(2)} previsto</div>`;
            // Nos essenciais a nota que ordena é a do IMDb (amostra bem maior);
            // nas demais telas cai para a da TMDB.
            const r = (movie.rating != null) ? movie.rating : movie.vote_average;
            if (r) {
                const src = movie.rating_source === 'imdb' ? 'IMDb' : 'TMDB';
                const critic = (movie.critic != null)
                    ? `<span class="rating-critic" title="nota da crítica (Metacritic/Rotten Tomatoes)">🍅 ${movie.critic}</span>`
                    : '';
                return `<div class="movie-rating" title="nota ${src}">⭐ ${Number(r).toFixed(1)}`
                     + `<span class="rating-src">${src}</span>${critic}</div>`;
            }
            return `<div class="movie-rating">—</div>`;
        }

        function explanationBlock(e) {
            if (!e) return '';
            const sigs = (e.signals || []).filter(s => s.share > 0.001);
            const segs = sigs.map(s =>
                `<span class="seg ${SIGNAL_CLASS[s.signal] || ''}" style="width:${(s.share*100).toFixed(0)}%"
                       title="${esc(s.label)} ${(s.share*100).toFixed(0)}%"></span>`).join('');
            const legend = sigs.map(s => `${esc(s.label)} ${(s.share*100).toFixed(0)}%`).join(' · ');
            const chips = (e.matched_keywords || []).map(k => `<span class="chip chip-kw">${esc(k)}</span>`).join('')
                + (e.matched_title_terms || []).map(t => `<span class="chip chip-title">${esc(t)}</span>`).join('');
            const cons = e.constraints
                ? Object.entries(e.constraints).map(([role, name]) =>
                    `<span class="chip chip-person">${role === 'director' ? '🎬' : '⭐'} ${esc(name)}</span>`).join('')
                : '';
            const conf = (e.confidence != null) ? e.confidence : e.relevance;
            const confClass = conf >= 70 ? 'conf-high' : conf >= 40 ? 'conf-mid' : 'conf-low';
            return `
                <div class="explain">
                    <div class="explain-rel" title="Confiança absoluta do match (quão bem o filme casa com a busca). À direita: posição e relevância relativa a esta busca.">
                        <div class="explain-rel-track"><div class="explain-rel-fill ${confClass}" style="width:${conf}%"></div></div>
                        <span class="explain-rel-num">${conf}% conf · #${e.position} · rel ${e.relevance}</span>
                    </div>
                    ${segs ? `<div class="sig-bar">${segs}</div><div class="sig-legend">${legend}</div>` : ''}
                    ${(chips || cons) ? `<div class="explain-chips">${cons}${chips}</div>` : ''}
                </div>`;
        }

        function renderGrid(gridId, movies) {
            const grid = document.getElementById(gridId);
            grid.innerHTML = '';
            movies.forEach(m => {
                const card = document.createElement('div');
                card.className = 'movie-card';
                const year = m.release_year ? ` (${m.release_year})` : '';
                const overview = m.overview ? `<div class="movie-overview">${esc(m.overview)}</div>` : '';
                const poster = m.poster || 'https://placehold.co/342x513/141414/e50914?text=Sem+P%C3%B4ster';
                // Chips "por quê": perfil usa `why`; parecidos usam `shared_genres`.
                const whyChips = (m.why && m.why.length) ? m.why : (m.shared_genres || []);
                card.innerHTML = `
                    <div class="poster-wrap">
                        ${m.rank ? `<span class="rank-badge">#${m.rank}</span>` : ''}
                        ${m.canon_rank ? `<span class="canon-badge" title="No cânone (Sight & Sound + clássicos)">🏛️</span>` : ''}
                        <img src="${poster}" alt="${esc(m.title)}" loading="lazy" decoding="async"
                             onerror="this.parentNode.classList.add('no-poster'); this.remove();">
                        <span class="poster-fallback">${esc(m.title)}</span>
                    </div>
                    <div class="movie-title">${esc(m.title)}${year}</div>
                    ${ratingBadge(m)}
                    ${whyChips.length ? `<div class="movie-why">${whyChips.map(w => `<span class="chip">${esc(w)}</span>`).join('')}</div>` : ''}
                    ${explanationBlock(m.explanation)}
                    ${overview}
                    ${m.tmdb_id ? `<div class="card-actions"><button class="btn-similar" type="button">🎬 Ver parecidos</button></div>` : ''}`;
                const simBtn = card.querySelector('.btn-similar');
                if (simBtn) simBtn.addEventListener('click', () => openSimilar(m.tmdb_id, m.title));
                // Pôster e título abrem a ficha completa do filme.
                if (m.tmdb_id) {
                    const open = () => openMovie(m.tmdb_id);
                    card.querySelector('.poster-wrap').addEventListener('click', open);
                    card.querySelector('.movie-title').addEventListener('click', open);
                }
                grid.appendChild(card);
            });
            lastGridMovies[gridId] = movies;   // para re-decorar ao mudar os streamings
            annotateStreaming(gridId, movies);
        }

        // ========== ABA 1: BUSCA ==========
        function setupPersonAutocomplete(inputId, dropdownId, role) {
            const input = document.getElementById(inputId);
            const dropdown = document.getElementById(dropdownId);
            let timer;
            input.addEventListener('input', () => {
                clearTimeout(timer);
                const q = input.value.trim();
                if (q.length < 2) { dropdown.style.display = 'none'; return; }
                timer = setTimeout(async () => {
                    try {
                        const people = await (await fetch(`/people?q=${encodeURIComponent(q)}&role=${role}`)).json();
                        dropdown.innerHTML = '';
                        if (!people.length) { dropdown.style.display = 'none'; return; }
                        people.forEach(p => {
                            const div = document.createElement('div');
                            div.className = 'autocomplete-item';
                            div.innerHTML = `<span>${esc(p.name)}</span><small>${p.credits} filmes</small>`;
                            div.addEventListener('click', () => {
                                input.value = p.name;
                                dropdown.style.display = 'none';
                            });
                            dropdown.appendChild(div);
                        });
                        dropdown.style.display = 'block';
                    } catch (e) { dropdown.style.display = 'none'; }
                }, 250);
            });
            document.addEventListener('click', e => {
                if (!input.contains(e.target) && !dropdown.contains(e.target))
                    dropdown.style.display = 'none';
            });
        }
        setupPersonAutocomplete('directorInput', 'directorDropdown', 'director');
        setupPersonAutocomplete('actorInput', 'actorDropdown', 'actor');

        async function doSearch() {
            const status = document.getElementById('searchStatus');
            const body = {
                query: document.getElementById('searchQuery').value.trim(),
                director: document.getElementById('directorInput').value.trim(),
                actor: document.getElementById('actorInput').value.trim(),
                genre: document.getElementById('filterGenre').value,
                language: document.getElementById('filterLang').value.trim(),
                year_min: document.getElementById('filterYearMin').value,
                year_max: document.getElementById('filterYearMax').value,
                n: 18,
            };
            if (!body.query && !body.director && !body.actor) {
                status.textContent = 'Descreva o filme ou escolha um diretor/ator.'; return;
            }
            status.textContent = 'Buscando...';
            skeletonGrid('searchGrid');
            try {
                const res = await fetch('/search', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (!res.ok) { status.textContent = data.error || 'Erro na busca.'; document.getElementById('searchGrid').innerHTML = ''; return; }
                const bits = [];
                if (data.director) bits.push(`diretor: ${data.director}`);
                if (data.actor) bits.push(`ator: ${data.actor}`);
                status.textContent = data.count
                    ? `${data.count} resultado(s)` + (bits.length ? ` · ${bits.join(' · ')}` : '')
                    : 'Nada encontrado.';
                renderGrid('searchGrid', data.results);
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }
        document.getElementById('searchBtn').addEventListener('click', doSearch);
        ['searchQuery', 'directorInput', 'actorInput'].forEach(id => {
            document.getElementById(id).addEventListener('keydown', e => {
                if (e.key === 'Enter') doSearch();
            });
        });

        // ========== ABA 2: ESCOLHER FILMES FAVORITOS ==========
        let pickSearchTimeout, popularOffset = 0, popularLoaded = false;
        const picked = new Map();   // tmdb_id -> {tmdb_id, title, release_year, poster}

        function renderPicked() {
            document.getElementById('pickCount').textContent = picked.size;
            document.getElementById('pickRecommendBtn').disabled = picked.size === 0;
            const row = document.getElementById('pickedRow');
            row.innerHTML = '';
            picked.forEach(f => {
                const chip = document.createElement('span');
                chip.className = 'picked-chip';
                chip.innerHTML = `${esc(f.title)} <b>&times;</b>`;
                chip.title = 'Remover';
                chip.addEventListener('click', () => togglePick(f));
                row.appendChild(chip);
            });
        }

        function togglePick(film) {
            const id = film.tmdb_id;
            if (picked.has(id)) picked.delete(id); else picked.set(id, film);
            const card = document.querySelector(`.pick-card[data-id="${id}"]`);
            if (card) card.classList.toggle('selected', picked.has(id));
            renderPicked();
        }

        function pickCard(film) {
            const card = document.createElement('div');
            card.className = 'pick-card' + (picked.has(film.tmdb_id) ? ' selected' : '');
            card.dataset.id = film.tmdb_id;
            const poster = film.poster || 'https://placehold.co/342x513/141414/e50914?text=%3F';
            const year = film.release_year ? ` (${film.release_year})` : '';
            card.innerHTML = `
                <div class="pick-poster">
                    <img src="${poster}" alt="${esc(film.title)}" loading="lazy" onerror="this.style.opacity=0">
                    <span class="pick-check">✓</span>
                    <button class="pick-info" type="button" title="Abrir a ficha do filme">ℹ️ Ficha</button>
                    <button class="pick-similar" type="button" title="Ver filmes parecidos">🎬 Parecidos</button>
                </div>
                <div class="pick-title">${esc(film.title)}${year}</div>`;
            // Botões do pôster não devem disparar a seleção do card.
            card.querySelector('.pick-similar').addEventListener('click', e => {
                e.stopPropagation();
                openSimilar(film.tmdb_id, film.title);
            });
            card.querySelector('.pick-info').addEventListener('click', e => {
                e.stopPropagation();
                openMovie(film.tmdb_id);
            });
            card.addEventListener('click', () => togglePick(film));
            return card;
        }

        async function loadPopular() {
            const grid = document.getElementById('popularGrid');
            const more = document.getElementById('pickMoreBtn');
            more.textContent = 'Carregando...';
            try {
                const films = await (await fetch(`/popular?n=42&offset=${popularOffset}`)).json();
                films.forEach(f => grid.appendChild(pickCard(f)));
                popularOffset += films.length;
                more.textContent = 'Mostrar mais filmes';
                more.style.display = films.length >= 42 ? 'block' : 'none';
            } catch (e) { grid.innerHTML = '<p class="hint">Erro ao carregar filmes.</p>'; }
        }
        document.getElementById('pickMoreBtn').addEventListener('click', loadPopular);

        // Busca para adicionar um filme específico que não está na grade.
        // Usa o mesmo motor da busca principal (inglês + TMDB + fuzzy).
        (function setupPickSearch() {
            const input = document.getElementById('pickSearch');
            const dropdown = document.getElementById('pickSearchDropdown');
            input.addEventListener('input', () => {
                clearTimeout(pickSearchTimeout);
                const q = input.value.trim();
                if (q.length < 2) { dropdown.style.display = 'none'; return; }
                pickSearchTimeout = setTimeout(async () => {
                    let results = [];
                    try {
                        const res = await fetch('/search', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ query: q, n: 8 })
                        });
                        results = (await res.json()).results || [];
                    } catch (e) { dropdown.style.display = 'none'; return; }
                    dropdown.innerHTML = '';
                    if (!results.length) { dropdown.style.display = 'none'; return; }
                    results.forEach(m => {
                        const div = document.createElement('div');
                        div.className = 'autocomplete-item';
                        const year = m.release_year ? ` (${m.release_year})` : '';
                        div.innerHTML = `<span>${esc(m.title)}${year}</span>`;
                        div.addEventListener('click', () => {
                            togglePick({ tmdb_id: m.tmdb_id, title: m.title,
                                         release_year: m.release_year, poster: m.poster });
                            input.value = ''; dropdown.style.display = 'none';
                        });
                        dropdown.appendChild(div);
                    });
                    dropdown.style.display = 'block';
                }, 280);
            });
            document.addEventListener('click', e => {
                if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.style.display = 'none';
            });
        })();

        async function runPickRecommend() {
            const status = document.getElementById('pick3Status');
            if (!picked.size) { status.textContent = 'Escolha pelo menos um filme.'; return; }
            activeRerun = runPickRecommend;
            status.textContent = 'Analisando seu gosto...';
            renderProfile(null, 'pick3Profile');
            skeletonGrid('pick3Grid');
            const body = { movie_ids: [...picked.keys()] };
            const sp = streamingParams();
            if (sp) { body.region = sp.region; body.providers = sp.providers; }
            try {
                const res = await fetch('/submit_ratings', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (res.ok) {
                    status.textContent = data.message || 'Recomendações geradas!';
                    renderProfile(data.profile, 'pick3Profile');
                    renderGrid('pick3Grid', data.recommendations);
                    document.getElementById('pick3Status').scrollIntoView({ behavior: 'smooth' });
                } else {
                    status.textContent = data.error || data.message || 'Erro ao processar.';
                    document.getElementById('pick3Grid').innerHTML = '';
                }
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }
        document.getElementById('pickRecommendBtn').addEventListener('click', runPickRecommend);

        // ========== ABA 3: RECOMENDAR LETTERBOXD ==========
        function renderProfile(p, boxId = 'recommendProfile') {
            const box = document.getElementById(boxId);
            if (!p) { box.style.display = 'none'; return; }
            const chips = arr => (arr || []).map(x => `<span class="chip">${esc(x)}</span>`).join('');
            const row = (label, arr) => (arr && arr.length)
                ? `<div class="profile-row"><span class="profile-label">${label}</span>${chips(arr)}</div>` : '';
            const interestRow = (p.interests && p.interests.length)
                ? `<div class="profile-row"><span class="profile-label">Interesses</span>`
                  + p.interests.map(x => `<span class="chip chip-interest">${esc(x)}</span>`).join('')
                  + `</div>` : '';
            box.innerHTML = `
                <div class="profile-head">🎭 Seu perfil de gosto
                    <small>${p.n_liked} filmes amados de ${p.n_rated} · nota média ⭐ ${p.avg_rating}</small>
                </div>
                ${interestRow}
                ${row('Diretores', p.directors)}
                ${row('Gêneros', p.genres)}
                ${row('Atores', p.actors)}
                ${row('Temas', p.themes)}
                ${row('Décadas', p.decades)}`;
            box.style.display = 'block';
        }

        async function runLetterboxd() {
            const fileInput = document.getElementById('ratingsFile');
            const status = document.getElementById('recommendStatus');
            if (!fileInput.files.length) { status.textContent = 'Selecione o ratings.csv.'; return; }
            activeRerun = runLetterboxd;
            status.textContent = 'Analisando seu perfil...';
            renderProfile(null);
            skeletonGrid('recommendGrid');
            const fd = new FormData();
            fd.append('ratings', fileInput.files[0]);
            const sp = streamingParams();
            if (sp) { fd.append('region', sp.region); fd.append('providers', sp.providers); }
            try {
                const res = await fetch('/recommend', { method: 'POST', body: fd });
                const data = await res.json();
                if (!res.ok) {
                    status.textContent = data.error || 'Erro ao recomendar.';
                    document.getElementById('recommendGrid').innerHTML = '';
                    return;
                }
                status.textContent = `${data.matched}/${data.total_rows} filmes casados `
                    + `(${Math.round(data.match_rate*100)}%) · estratégia: ${data.method}`;
                renderProfile(data.profile);
                renderGrid('recommendGrid', data.recommendations);
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }
        document.getElementById('recommendBtn').addEventListener('click', runLetterboxd);

        // ========== STREAMING (onde assistir) ==========
        const lastGridMovies = {};   // gridId -> filmes renderizados (p/ re-decorar)
        function loadMyProviders() {
            try { return new Set((JSON.parse(localStorage.getItem('myProviders') || '[]')).map(String)); }
            catch (e) { return new Set(); }
        }
        const myProviders = loadMyProviders();
        let streamingOnly = localStorage.getItem('streamingOnly') === '1';
        let providerCatalog = [];    // [{id, name, logo}] disponíveis na região
        let streamingRegion = 'BR';
        let activeRerun = null;      // re-executa a última consulta de descoberta (tab)
        let modalRerun = null;       // idem, para o modal de parecidos

        // Parâmetros de streaming p/ o servidor — só quando "só o que tenho" está ligado.
        function streamingParams() {
            return (streamingOnly && myProviders.size)
                ? { region: streamingRegion, providers: [...myProviders].join(',') }
                : null;
        }

        function similarUrl(tmdbId, n) {
            const sp = streamingParams();
            let url = `/similar/${tmdbId}?n=${n}`;
            if (sp) url += `&region=${encodeURIComponent(sp.region)}&providers=${encodeURIComponent(sp.providers)}`;
            return url;
        }

        function saveStreaming() {
            localStorage.setItem('myProviders', JSON.stringify([...myProviders]));
            localStorage.setItem('streamingOnly', streamingOnly ? '1' : '0');
            document.getElementById('streamingCount').textContent = myProviders.size;
        }

        // Mudou serviço/toggle: o filtro é no servidor, então re-roda a consulta ativa.
        function onStreamingChange() {
            saveStreaming();
            let reran = false;
            if (modalRerun) { modalRerun(); reran = true; }
            if (activeRerun) { activeRerun(); reran = true; }
            if (!reran) reannotateAll();   // nada pra re-rodar: só atualiza os selos
        }

        function reannotateAll() {
            Object.keys(lastGridMovies).forEach(id => annotateStreaming(id, lastGridMovies[id]));
        }

        function renderProviderChips() {
            const box = document.getElementById('streamingChips');
            box.innerHTML = '';
            providerCatalog.forEach(p => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'stream-chip' + (myProviders.has(String(p.id)) ? ' on' : '');
                chip.innerHTML = (p.logo ? `<img src="${p.logo}" alt="" loading="lazy">` : '')
                    + `<span>${esc(p.name)}</span>`;
                chip.addEventListener('click', () => {
                    const id = String(p.id);
                    if (myProviders.has(id)) myProviders.delete(id); else myProviders.add(id);
                    chip.classList.toggle('on', myProviders.has(id));
                    onStreamingChange();
                });
                box.appendChild(chip);
            });
        }

        function loadProviders() {
            fetch('/providers').then(r => r.json()).then(d => {
                providerCatalog = d.providers || [];
                streamingRegion = d.region || 'BR';
                if (!providerCatalog.length) return;   // sem TMDB → recurso indisponível
                document.getElementById('streamingBar').style.display = 'block';
                document.getElementById('streamingCount').textContent = myProviders.size;
                renderProviderChips();

                const chips = document.getElementById('streamingChips');
                const btn = document.getElementById('streamingToggleBtn');
                btn.addEventListener('click', () => {
                    const open = chips.style.display !== 'none';
                    chips.style.display = open ? 'none' : 'flex';
                    btn.setAttribute('aria-expanded', String(!open));
                });

                const only = document.getElementById('streamingOnly');
                only.checked = streamingOnly;
                only.addEventListener('change', () => {
                    streamingOnly = only.checked; onStreamingChange();
                });
            }).catch(() => {});
        }

        // Decora os cards de uma grade com os provedores dos filmes; filtra se "só o que tenho".
        async function annotateStreaming(gridId, movies) {
            const grid = document.getElementById(gridId);
            if (!grid || !movies) return;
            const clear = () => [...grid.children].forEach(card => {
                const r = card.querySelector('.stream-row'); if (r) r.remove();
                card.style.display = '';
            });
            // Só busca disponibilidade depois que o usuário escolhe ao menos um serviço.
            if (!providerCatalog.length || !myProviders.size) { clear(); return; }

            const ids = movies.map(m => m.tmdb_id).filter(Boolean);
            if (!ids.length) return;
            let map = {};
            try {
                const res = await fetch('/watch_providers', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ movie_ids: ids })
                });
                map = (await res.json()).providers || {};
            } catch (e) { return; }

            // A grade pode ter sido re-renderizada enquanto buscávamos — confere o tamanho.
            const cards = [...grid.children];
            if (cards.length !== movies.length) return;
            cards.forEach((card, i) => {
                const m = movies[i];
                const provs = (m && map[String(m.tmdb_id)]) || [];
                const old = card.querySelector('.stream-row'); if (old) old.remove();
                if (provs.length) {
                    const row = document.createElement('div');
                    row.className = 'stream-row';
                    row.innerHTML = provs.slice(0, 6).map(p => {
                        const mine = myProviders.has(String(p.id));
                        const cls = 'stream-logo' + (mine ? ' mine' : '');
                        return p.logo
                            ? `<img class="${cls}" src="${p.logo}" title="${esc(p.name)}" alt="${esc(p.name)}" loading="lazy">`
                            : `<span class="stream-name${mine ? ' mine' : ''}" title="${esc(p.name)}">${esc(p.name)}</span>`;
                    }).join('');
                    const actions = card.querySelector('.card-actions');
                    if (actions) card.insertBefore(row, actions); else card.appendChild(row);
                }
                card.style.display = '';   // filtro agora é no servidor; aqui só decoramos
            });
        }

        // ========== PARECIDOS (item-to-item) ==========
        const similarModal = document.getElementById('similarModal');

        function openSimilar(tmdbId, title) {
            modalRerun = () => openSimilar(tmdbId, title);
            similarModal.style.display = 'flex';
            bringToFront(similarModal);
            document.body.classList.add('modal-open');
            const filtered = !!streamingParams();
            document.getElementById('similarTitle').textContent = 'Parecidos com ' + (title || 'este filme');
            document.getElementById('similarStatus').textContent = 'Buscando parecidos...';
            skeletonGrid('similarGrid', 6);
            similarModal.querySelector('.modal').scrollTop = 0;
            fetch(similarUrl(tmdbId, 12))
                .then(r => r.json().then(data => ({ ok: r.ok, data })))
                .then(({ ok, data }) => {
                    const status = document.getElementById('similarStatus');
                    if (!ok) { status.textContent = data.error || 'Erro ao buscar parecidos.'; document.getElementById('similarGrid').innerHTML = ''; return; }
                    status.textContent = data.count
                        ? `${data.count} parecido(s)${filtered ? ' nos seus streamings' : ''}`
                        : (filtered ? 'Nenhum parecido nos seus streamings.' : 'Nada encontrado.');
                    renderGrid('similarGrid', data.recommendations || []);
                })
                .catch(e => { document.getElementById('similarStatus').textContent = 'Erro: ' + e.message; });
        }

        function closeSimilar() {
            similarModal.style.display = 'none';
            // A ficha pode estar aberta por baixo — só libera o scroll se não estiver.
            if (movieModal.style.display === 'none') document.body.classList.remove('modal-open');
            modalRerun = null;
        }
        document.getElementById('similarClose').addEventListener('click', closeSimilar);
        similarModal.addEventListener('click', e => { if (e.target === similarModal) closeSimilar(); });
        document.addEventListener('keydown', e => {
            if (e.key !== 'Escape') return;
            const simOpen = similarModal.style.display !== 'none';
            const movOpen = movieModal.style.display !== 'none';
            if (simOpen && movOpen) {
                // Fecha o que está na frente (maior z-index).
                (+similarModal.style.zIndex >= +movieModal.style.zIndex ? closeSimilar : closeMovie)();
            } else if (simOpen) closeSimilar();
            else if (movOpen) closeMovie();
        });

        // --- Aba "Parecidos": escolhe um filme e mostra os parecidos inline ---
        async function loadSimilarInto(tmdbId, title) {
            activeRerun = () => loadSimilarInto(tmdbId, title);
            const status = document.getElementById('similarTabStatus');
            const filtered = !!streamingParams();
            const sufixo = filtered ? ' nos seus streamings' : '';
            status.textContent = `Buscando parecidos com "${title}"${sufixo}...`;
            skeletonGrid('similarTabGrid', 6);
            try {
                const res = await fetch(similarUrl(tmdbId, 18));
                const data = await res.json();
                if (!res.ok) { status.textContent = data.error || 'Erro ao buscar parecidos.'; document.getElementById('similarTabGrid').innerHTML = ''; return; }
                status.textContent = data.count
                    ? `${data.count} parecido(s) com "${title}"${sufixo}`
                    : (filtered ? 'Nenhum parecido nos seus streamings.' : 'Nada encontrado.');
                renderGrid('similarTabGrid', data.recommendations || []);
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }

        (function setupSimilarSearch() {
            const input = document.getElementById('similarSearch');
            const dropdown = document.getElementById('similarSearchDropdown');
            let timer;
            input.addEventListener('input', () => {
                clearTimeout(timer);
                const q = input.value.trim();
                if (q.length < 2) { dropdown.style.display = 'none'; return; }
                timer = setTimeout(async () => {
                    let results = [];
                    try {
                        const res = await fetch('/search', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ query: q, n: 8 })
                        });
                        results = (await res.json()).results || [];
                    } catch (e) { dropdown.style.display = 'none'; return; }
                    dropdown.innerHTML = '';
                    if (!results.length) { dropdown.style.display = 'none'; return; }
                    results.forEach(m => {
                        const div = document.createElement('div');
                        div.className = 'autocomplete-item';
                        const year = m.release_year ? ` (${m.release_year})` : '';
                        div.innerHTML = `<span>${esc(m.title)}${year}</span>`;
                        div.addEventListener('click', () => {
                            input.value = m.title;
                            dropdown.style.display = 'none';
                            loadSimilarInto(m.tmdb_id, m.title);
                        });
                        dropdown.appendChild(div);
                    });
                    dropdown.style.display = 'block';
                }, 280);
            });
            document.addEventListener('click', e => {
                if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.style.display = 'none';
            });
        })();

        // ========== FICHA DO FILME ==========
        const movieModal = document.getElementById('movieModal');
        const PROVIDER_KINDS = [
            ['flatrate', '📺 Incluído na assinatura'],
            ['free', '🆓 Grátis'],
            ['ads', '📺 Grátis com anúncios'],
            ['rent', '💸 Alugar'],
            ['buy', '🛒 Comprar'],
        ];

        function runtimeFmt(min) {
            if (!min) return '';
            const h = Math.floor(min / 60), m = min % 60;
            return h ? `${h}h${m ? ' ' + m + 'min' : ''}` : `${m}min`;
        }

        function providerGroup(label, provs) {
            if (!provs || !provs.length) return '';
            const chips = provs.map(p => {
                const mine = myProviders.has(String(p.id)) ? ' mine' : '';
                return `<span class="sheet-provider${mine}" title="${esc(p.name)}">`
                    + (p.logo ? `<img src="${p.logo}" alt="" loading="lazy">` : '')
                    + `${esc(p.name)}</span>`;
            }).join('');
            return `<div class="sheet-prov-group"><span class="sheet-prov-label">${label}</span>
                    <div class="sheet-prov-chips">${chips}</div></div>`;
        }

        function renderMovieSheet(data) {
            const d = data.details, pv = data.providers || {};
            const box = document.getElementById('movieSheetBody');
            const year = d.release_year ? ` <span class="sheet-year">(${d.release_year})</span>` : '';
            const meta = [
                d.vote_average
                    ? `⭐ ${Number(d.vote_average).toFixed(1)}`
                      + (d.vote_count ? ` (${Number(d.vote_count).toLocaleString('pt-BR')} votos)` : '')
                    : '',
                runtimeFmt(d.runtime),
                d.release_date ? d.release_date.split('-').reverse().join('/') : '',
            ].filter(Boolean).join(' · ');
            const groups = PROVIDER_KINDS.map(([k, label]) => providerGroup(label, pv[k])).join('');
            const origTitle = (d.original_title && d.original_title !== d.title)
                ? `<p class="sheet-orig">${esc(d.original_title)}</p>` : '';
            const cast = (d.cast || []).map(c => `
                <div class="cast-card">
                    ${c.photo ? `<img src="${c.photo}" alt="${esc(c.name)}" loading="lazy">`
                              : `<div class="cast-photo-fallback">${esc((c.name || '?')[0])}</div>`}
                    <div class="cast-name">${esc(c.name)}</div>
                    ${c.character ? `<div class="cast-role">${esc(c.character)}</div>` : ''}
                </div>`).join('');
            box.innerHTML = `
                ${d.backdrop ? `<div class="sheet-hero" style="background-image:url('${d.backdrop}')"></div>` : ''}
                <div class="sheet-main${d.backdrop ? ' has-hero' : ''}">
                    <img class="sheet-poster" alt="${esc(d.title)}"
                         src="${d.poster || 'https://placehold.co/342x513/141414/e50914?text=Sem+P%C3%B4ster'}">
                    <div class="sheet-info">
                        <h2 id="sheetTitle">${esc(d.title)}${year}</h2>
                        ${origTitle}
                        ${d.tagline ? `<p class="sheet-tagline">${esc(d.tagline)}</p>` : ''}
                        ${meta ? `<div class="sheet-meta">${meta}</div>` : ''}
                        ${(d.genres || []).length
                            ? `<div class="sheet-genres">${d.genres.map(g => `<span class="chip">${esc(g)}</span>`).join('')}</div>` : ''}
                        ${(d.directors || []).length
                            ? `<p class="sheet-director">🎬 Direção: <b>${d.directors.map(x => esc(x)).join(', ')}</b></p>` : ''}
                        ${d.collection ? `<p class="sheet-collection">📀 Franquia: ${esc(d.collection.name)}</p>` : ''}
                        <div class="sheet-actions">
                            <button class="btn-similar" type="button" id="sheetSimilarBtn">🎬 Ver parecidos</button>
                            <span class="sheet-list-wrap">
                                <button class="btn-similar" type="button" id="sheetListBtn">➕ Adicionar a lista</button>
                                <div class="sheet-list-menu autocomplete-dropdown" id="sheetListMenu"></div>
                            </span>
                            ${d.collection ? `<button class="btn-similar" type="button" id="sheetCollBtn"
                                title="Criar lista com toda a franquia ${esc(d.collection.name)}">📀 Lista da franquia</button>` : ''}
                            <span class="rate-status" id="sheetActionStatus"></span>
                        </div>
                    </div>
                </div>
                <div class="sheet-section sheet-rating">
                    <h3>Minha avaliação</h3>
                    <div class="rate-row">
                        <div class="stars" id="rateStars" title="Clique para dar a nota (meia estrela vale)">
                            <span class="stars-bg">★★★★★<span class="stars-fill" id="rateStarsFill">★★★★★</span></span>
                        </div>
                        <span class="rate-value" id="rateValue"></span>
                        <button class="rate-heart" id="rateHeart" type="button" title="Curti este filme">♥</button>
                        <label class="rate-date-label">assisti em
                            <input type="date" id="rateDate" class="mini-input rate-date">
                        </label>
                    </div>
                    <textarea id="rateReview" class="rate-review" rows="3"
                              placeholder="Escreva uma resenha (opcional)..."></textarea>
                    <div class="rate-actions">
                        <button class="btn-primary" id="rateSave" type="button">Salvar no diário</button>
                        <button class="btn-secondary rate-remove" id="rateRemove" type="button">Remover do diário</button>
                        <span class="rate-status" id="rateStatus"></span>
                    </div>
                </div>
                ${d.overview ? `<div class="sheet-section"><h3>Sinopse</h3>
                    <p class="sheet-overview">${esc(d.overview)}</p></div>` : ''}
                <div class="sheet-section">
                    <h3>Onde assistir <small>região ${esc(data.region || 'BR')}</small></h3>
                    ${groups || '<p class="sheet-empty">Não encontrado em streaming, aluguel ou compra na sua região.</p>'}
                    ${pv.link ? `<a class="sheet-source" href="${pv.link}" target="_blank" rel="noopener">fonte: JustWatch via TMDB ↗</a>` : ''}
                </div>
                <div class="sheet-section sheet-versions">
                    <h3>Versões <small>cortes existentes — curadoria sua</small></h3>
                    <div id="versionsBox"></div>
                    <div id="versionForm" class="version-form" style="display:none">
                        <div class="version-form-row">
                            <input type="text" id="vName" class="text-input"
                                   placeholder="Nome da versão (ex: Final Cut, Versão de Cinema, Director's Cut...)">
                            <input type="number" id="vRuntime" class="mini-input" placeholder="min" min="1" max="1000">
                        </div>
                        <textarea id="vNotes" class="rate-review" rows="2"
                                  placeholder="O que muda nessa versão? Onde encontrar? (opcional)"></textarea>
                        <div class="rate-actions">
                            <label class="v-best-label"><input type="checkbox" id="vBest"> 🏆 é a melhor versão</label>
                            <button type="button" class="btn-primary" id="vSave">Salvar versão</button>
                            <button type="button" class="btn-secondary v-cancel" id="vCancel">Cancelar</button>
                        </div>
                    </div>
                    <button type="button" class="btn-secondary version-add" id="versionAddBtn">➕ Adicionar versão</button>
                </div>
                ${cast ? `<div class="sheet-section"><h3>Elenco</h3><div class="cast-strip">${cast}</div></div>` : ''}`;
            const simBtn = document.getElementById('sheetSimilarBtn');
            if (simBtn) simBtn.addEventListener('click', () => openSimilar(d.tmdb_id, d.title));
            setupRatingWidget(d, data.my_rating);
            setupListButtons(d);
            setupVersions(d, data.versions);
        }

        // Seção de versões da ficha (cortes existentes + qual é a melhor).
        function setupVersions(d, versions) {
            const box = document.getElementById('versionsBox');
            const form = document.getElementById('versionForm');
            const addBtn = document.getElementById('versionAddBtn');
            let editingId = null;
            let current = versions || [];

            function render() {
                box.innerHTML = '';
                if (!current.length) {
                    box.innerHTML = '<p class="sheet-empty">Nenhuma versão registrada — se este filme tem cortes diferentes (Director’s Cut, versão estendida...), adicione e marque a melhor.</p>';
                    return;
                }
                current.forEach(v => {
                    const el = document.createElement('div');
                    el.className = 'version-card' + (v.is_best ? ' best' : '');
                    el.innerHTML = `
                        <div class="version-head">
                            <span class="version-name">${esc(v.name)}</span>
                            ${v.runtime ? `<span class="version-runtime">${runtimeFmt(v.runtime)}</span>` : ''}
                            ${v.is_best ? '<span class="version-best">🏆 melhor versão</span>' : ''}
                            <span class="version-actions">
                                <button type="button" class="v-edit" title="Editar">✎</button>
                                <button type="button" class="v-del" title="Remover">&times;</button>
                            </span>
                        </div>
                        ${v.notes ? `<p class="version-notes">${esc(v.notes)}</p>` : ''}`;
                    el.querySelector('.v-edit').addEventListener('click', () => {
                        editingId = v.version_id;
                        document.getElementById('vName').value = v.name;
                        document.getElementById('vRuntime').value = v.runtime || '';
                        document.getElementById('vNotes').value = v.notes || '';
                        document.getElementById('vBest').checked = v.is_best;
                        form.style.display = ''; addBtn.style.display = 'none';
                    });
                    el.querySelector('.v-del').addEventListener('click', async () => {
                        if (!confirm(`Remover a versão “${v.name}”?`)) return;
                        await fetch(`/versions/${v.version_id}`, { method: 'DELETE' });
                        refresh();
                    });
                    box.appendChild(el);
                });
            }

            async function refresh() {
                try {
                    current = (await (await fetch(`/versions/${d.tmdb_id}`)).json()).versions || [];
                } catch (e) { /* mantém o que está na tela */ }
                render();
            }

            function resetForm() {
                editingId = null;
                ['vName', 'vRuntime', 'vNotes'].forEach(id => document.getElementById(id).value = '');
                document.getElementById('vBest').checked = false;
                form.style.display = 'none';
                addBtn.style.display = '';
            }

            addBtn.addEventListener('click', () => {
                resetForm();
                form.style.display = ''; addBtn.style.display = 'none';
                document.getElementById('vName').focus();
            });
            document.getElementById('vCancel').addEventListener('click', resetForm);
            document.getElementById('vSave').addEventListener('click', async () => {
                const name = document.getElementById('vName').value.trim();
                if (!name) { document.getElementById('vName').focus(); return; }
                const body = {
                    tmdb_id: d.tmdb_id, name,
                    runtime: document.getElementById('vRuntime').value || null,
                    notes: document.getElementById('vNotes').value,
                    is_best: document.getElementById('vBest').checked,
                };
                if (editingId) body.version_id = editingId;
                try {
                    const res = await fetch('/versions', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!res.ok) {
                        const out = await res.json();
                        alert(out.error || 'Erro ao salvar a versão.');
                        return;
                    }
                    resetForm();
                    refresh();
                } catch (e) { alert('Erro: ' + e.message); }
            });

            render();
        }

        // Botões de lista da ficha: menu "adicionar a..." + importar franquia.
        function setupListButtons(d) {
            const btn = document.getElementById('sheetListBtn');
            const menu = document.getElementById('sheetListMenu');
            const status = document.getElementById('sheetActionStatus');

            async function addToList(listId, listName) {
                menu.style.display = 'none';
                try {
                    const res = await fetch(`/lists/${listId}/items`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tmdb_id: d.tmdb_id, title: d.title,
                                               release_year: d.release_year, poster: d.poster }),
                    });
                    const out = await res.json();
                    status.textContent = res.ok
                        ? (out.added ? `✓ adicionado a “${listName}”` : `já está em “${listName}”`)
                        : (out.error || 'Erro ao adicionar.');
                } catch (e) { status.textContent = 'Erro: ' + e.message; }
            }

            btn.addEventListener('click', async () => {
                if (menu.style.display === 'block') { menu.style.display = 'none'; return; }
                menu.innerHTML = '<div class="autocomplete-item"><span>Carregando...</span></div>';
                menu.style.display = 'block';
                let lists = [];
                try { lists = (await (await fetch('/lists')).json()).lists || []; }
                catch (e) { /* segue com só "nova lista" */ }
                menu.innerHTML = '';
                lists.forEach(l => {
                    const it = document.createElement('div');
                    it.className = 'autocomplete-item';
                    it.innerHTML = `<span>${esc(l.name)}</span><small>${l.n_items} filmes</small>`;
                    it.addEventListener('click', () => addToList(l.list_id, l.name));
                    menu.appendChild(it);
                });
                const nw = document.createElement('div');
                nw.className = 'autocomplete-item';
                nw.innerHTML = '<span>➕ Nova lista...</span>';
                nw.addEventListener('click', async () => {
                    menu.style.display = 'none';
                    const name = prompt('Nome da nova lista:');
                    if (!name || !name.trim()) return;
                    try {
                        const res = await fetch('/lists', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: name.trim() }),
                        });
                        const out = await res.json();
                        if (!res.ok) { status.textContent = out.error || 'Erro ao criar.'; return; }
                        addToList(out.list.list_id, out.list.name);
                    } catch (e) { status.textContent = 'Erro: ' + e.message; }
                });
                menu.appendChild(nw);
            });

            const collBtn = document.getElementById('sheetCollBtn');
            if (collBtn) collBtn.addEventListener('click', async () => {
                status.textContent = 'Criando lista da franquia...';
                try {
                    const res = await fetch('/lists/from_collection', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ collection_id: d.collection.id }),
                    });
                    const out = await res.json();
                    status.textContent = res.ok
                        ? `✓ “${out.list.name}” criada com ${out.list.items.length} filmes — veja em Meu › Listas`
                        : (out.error || 'Erro ao criar a lista.');
                } catch (e) { status.textContent = 'Erro: ' + e.message; }
            });
        }

        // Widget de avaliação da ficha (estrelas 0,5–5 + ❤ + resenha + data).
        function setupRatingWidget(d, my) {
            let rating = (my && my.rating) || 0;
            let liked = !!(my && my.liked);
            const stars = document.getElementById('rateStars');
            const fill = document.getElementById('rateStarsFill');
            const value = document.getElementById('rateValue');
            const heart = document.getElementById('rateHeart');
            const dateIn = document.getElementById('rateDate');
            const review = document.getElementById('rateReview');
            const removeBtn = document.getElementById('rateRemove');
            const status = document.getElementById('rateStatus');

            if (my && my.watched_date) dateIn.value = my.watched_date;
            if (my && my.review) review.value = my.review;
            removeBtn.style.display = my ? '' : 'none';

            const paint = r => {
                fill.style.width = (r / 5 * 100) + '%';
                value.textContent = r ? r.toFixed(1).replace('.', ',') : '';
            };
            const paintHeart = () => heart.classList.toggle('on', liked);
            paint(rating); paintHeart();

            // Posição do mouse/toque → nota em meias-estrelas.
            const ratingFromEvent = e => {
                const rect = stars.getBoundingClientRect();
                const frac = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
                return Math.max(0.5, Math.ceil(frac * 10) / 2);
            };
            stars.addEventListener('mousemove', e => paint(ratingFromEvent(e)));
            stars.addEventListener('mouseleave', () => paint(rating));
            stars.addEventListener('click', e => {
                const r = ratingFromEvent(e);
                rating = (r === rating) ? 0 : r;   // clicar na mesma nota limpa
                paint(rating);
            });
            heart.addEventListener('click', () => { liked = !liked; paintHeart(); });

            document.getElementById('rateSave').addEventListener('click', async () => {
                status.textContent = 'Salvando...';
                try {
                    const res = await fetch('/ratings', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            tmdb_id: d.tmdb_id, rating: rating || null, liked,
                            review: review.value, watched_date: dateIn.value || null,
                            title: d.title, release_year: d.release_year, poster: d.poster,
                        }),
                    });
                    const out = await res.json();
                    if (!res.ok) { status.textContent = out.error || 'Erro ao salvar.'; return; }
                    status.textContent = 'Salvo no diário ✓';
                    removeBtn.style.display = '';
                } catch (e) { status.textContent = 'Erro: ' + e.message; }
            });
            removeBtn.addEventListener('click', async () => {
                status.textContent = 'Removendo...';
                try {
                    const res = await fetch(`/ratings/${d.tmdb_id}`, { method: 'DELETE' });
                    const out = await res.json();
                    if (!res.ok) { status.textContent = out.error || 'Erro ao remover.'; return; }
                    rating = 0; liked = false; review.value = ''; dateIn.value = '';
                    paint(0); paintHeart();
                    removeBtn.style.display = 'none';
                    status.textContent = 'Removido do diário.';
                } catch (e) { status.textContent = 'Erro: ' + e.message; }
            });
        }

        async function openMovie(tmdbId) {
            movieModal.style.display = 'flex';
            bringToFront(movieModal);
            document.body.classList.add('modal-open');
            movieModal.querySelector('.modal').scrollTop = 0;
            const box = document.getElementById('movieSheetBody');
            box.innerHTML = '<p class="status sheet-loading">Carregando ficha...</p>';
            try {
                const res = await fetch(`/movie/${tmdbId}?region=${encodeURIComponent(streamingRegion)}`);
                const data = await res.json();
                if (!res.ok) {
                    box.innerHTML = `<p class="status sheet-loading">${esc(data.error || 'Erro ao carregar a ficha.')}</p>`;
                    return;
                }
                renderMovieSheet(data);
            } catch (e) {
                box.innerHTML = `<p class="status sheet-loading">Erro: ${esc(e.message)}</p>`;
            }
        }

        function closeMovie() {
            movieModal.style.display = 'none';
            if (similarModal.style.display === 'none') document.body.classList.remove('modal-open');
        }
        document.getElementById('movieClose').addEventListener('click', closeMovie);
        movieModal.addEventListener('click', e => { if (e.target === movieModal) closeMovie(); });

        // ========== ABA: ASSISTIDOS (diário estilo Letterboxd) ==========
        let watchedRatings = [];

        function starsHtml(r) {
            return `<span class="stars stars-small"><span class="stars-bg">★★★★★<span class="stars-fill" style="width:${(r / 5 * 100)}%">★★★★★</span></span></span>`;
        }

        function diaryCard(r) {
            const card = document.createElement('div');
            card.className = 'diary-card';
            const poster = r.poster
                || `https://placehold.co/342x513/141414/e50914?text=${encodeURIComponent((r.title || '?').slice(0, 40))}`;
            const year = r.release_year ? ` (${r.release_year})` : '';
            const dateBr = r.watched_date ? r.watched_date.split('-').reverse().join('/') : '';
            card.innerHTML = `
                <img class="diary-poster" src="${poster}" alt="${esc(r.title)}" loading="lazy">
                <div class="diary-body">
                    <div class="diary-title">${esc(r.title)}${year}</div>
                    <div class="diary-stars">
                        ${r.rating ? starsHtml(r.rating) : '<span class="diary-norating">sem nota</span>'}
                        ${r.liked ? '<span class="diary-heart">♥</span>' : ''}
                    </div>
                    ${dateBr ? `<div class="diary-date">📅 assisti em ${dateBr}</div>` : ''}
                    ${r.review ? `<div class="diary-review">“${esc(r.review)}”</div>` : ''}
                </div>`;
            card.addEventListener('click', () => openMovie(r.tmdb_id));
            return card;
        }

        function renderWatched() {
            const grid = document.getElementById('watchedGrid');
            const sort = document.getElementById('watchedSort').value;
            const rows = [...watchedRatings];
            if (sort === 'rating') rows.sort((a, b) => (b.rating || 0) - (a.rating || 0));
            else if (sort === 'title') rows.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
            // 'watched': já vem do servidor por data assistida (recentes primeiro)
            grid.innerHTML = '';
            rows.forEach(r => grid.appendChild(diaryCard(r)));
        }

        async function loadWatched() {
            const status = document.getElementById('watchedStatus');
            try {
                const data = await (await fetch('/ratings')).json();
                watchedRatings = data.ratings || [];
                document.getElementById('watchedRecBtn').disabled =
                    !watchedRatings.some(r => r.rating || r.liked);
                status.textContent = watchedRatings.length
                    ? `${watchedRatings.length} filme(s) no seu diário`
                    : 'Diário vazio — abra a ficha de um filme e salve uma avaliação.';
                renderWatched();
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }
        document.getElementById('watchedSort').addEventListener('change', renderWatched);

        async function runHistoryRecommend() {
            const status = document.getElementById('watchedStatus');
            activeRerun = runHistoryRecommend;
            status.textContent = 'Analisando seu diário...';
            renderProfile(null, 'watchedProfile');
            skeletonGrid('watchedRecGrid');
            const body = { n: 15 };
            const sp = streamingParams();
            if (sp) { body.region = sp.region; body.providers = sp.providers; }
            try {
                const res = await fetch('/recommend_history', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json();
                if (!res.ok) { status.textContent = data.error || 'Erro ao recomendar.'; document.getElementById('watchedRecGrid').innerHTML = ''; return; }
                status.textContent = `Recomendações a partir de ${data.rated_count} filme(s) avaliado(s)`;
                renderProfile(data.profile, 'watchedProfile');
                renderGrid('watchedRecGrid', data.recommendations);
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }
        document.getElementById('watchedRecBtn').addEventListener('click', runHistoryRecommend);

        // ========== ABA: LISTAS (ordem de assistir) ==========
        let currentListId = null;
        let currentListItems = [];

        function showListsHome() {
            document.getElementById('listDetail').style.display = 'none';
            document.getElementById('listsHome').style.display = '';
            currentListId = null;
            loadLists();
        }

        async function loadLists() {
            const status = document.getElementById('listsStatus');
            const grid = document.getElementById('listsGrid');
            try {
                const data = await (await fetch('/lists')).json();
                const lists = data.lists || [];
                status.textContent = lists.length
                    ? `${lists.length} lista(s) — clique para abrir`
                    : 'Nenhuma lista ainda — crie a primeira acima.';
                grid.innerHTML = '';
                lists.forEach(l => grid.appendChild(listCard(l)));
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }

        function listCard(l) {
            const card = document.createElement('div');
            card.className = 'list-card';
            const posters = (l.posters || [])
                .map(p => `<img src="${p}" alt="" loading="lazy">`).join('');
            card.innerHTML = `
                <div class="list-collage">${posters || '<span class="list-collage-empty">🎞️</span>'}</div>
                <div class="list-card-body">
                    <div class="list-card-name">${esc(l.name)}</div>
                    <div class="list-card-meta">${l.n_items} filme(s)</div>
                </div>
                <button class="list-del" type="button" title="Apagar lista">&times;</button>`;
            card.querySelector('.list-del').addEventListener('click', async e => {
                e.stopPropagation();
                if (!confirm(`Apagar a lista “${l.name}”?`)) return;
                await fetch(`/lists/${l.list_id}`, { method: 'DELETE' });
                loadLists();
            });
            card.addEventListener('click', () => openList(l.list_id));
            return card;
        }

        document.getElementById('newListBtn').addEventListener('click', async () => {
            const input = document.getElementById('newListName');
            const name = input.value.trim();
            if (!name) { input.focus(); return; }
            try {
                const res = await fetch('/lists', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name }),
                });
                const out = await res.json();
                if (!res.ok) {
                    document.getElementById('listsStatus').textContent = out.error || 'Erro.';
                    return;
                }
                input.value = '';
                openList(out.list.list_id);
            } catch (e) { document.getElementById('listsStatus').textContent = 'Erro: ' + e.message; }
        });
        document.getElementById('newListName').addEventListener('keydown', e => {
            if (e.key === 'Enter') document.getElementById('newListBtn').click();
        });

        async function openList(id) {
            try {
                const res = await fetch(`/lists/${id}`);
                const lst = await res.json();
                if (!res.ok) {
                    document.getElementById('listsStatus').textContent = lst.error || 'Erro.';
                    return;
                }
                currentListId = id;
                currentListItems = lst.items || [];
                document.getElementById('listsHome').style.display = 'none';
                document.getElementById('listDetail').style.display = '';
                document.getElementById('listDetailName').textContent = lst.name;
                renderListItems();
            } catch (e) { document.getElementById('listsStatus').textContent = 'Erro: ' + e.message; }
        }
        document.getElementById('listBackBtn').addEventListener('click', showListsHome);

        function renderListItems() {
            document.getElementById('listDetailCount').textContent = currentListItems.length
                ? `${currentListItems.length} filme(s) — arraste (ou use ▲▼) para definir a ordem de assistir`
                : 'Lista vazia — abra a ficha de um filme e use “➕ Adicionar a lista”.';
            const box = document.getElementById('listItems');
            box.innerHTML = '';
            currentListItems.forEach((it, i) => box.appendChild(listItemRow(it, i)));
        }

        function listItemRow(it, i) {
            const row = document.createElement('div');
            row.className = 'list-item';
            row.draggable = true;
            row.dataset.id = it.tmdb_id;
            const poster = it.poster
                || `https://placehold.co/342x513/141414/e50914?text=${encodeURIComponent((it.title || '?').slice(0, 30))}`;
            const year = it.release_year ? ` (${it.release_year})` : '';
            row.innerHTML = `
                <span class="list-pos">${i + 1}</span>
                <img class="list-item-poster" src="${poster}" alt="" loading="lazy">
                <div class="list-item-title">${esc(it.title)}${year}</div>
                <span class="list-item-actions">
                    <button class="list-move" type="button" data-dir="-1" title="Subir">▲</button>
                    <button class="list-move" type="button" data-dir="1" title="Descer">▼</button>
                    <button class="list-item-del" type="button" title="Remover da lista">&times;</button>
                </span>`;
            const open = () => openMovie(it.tmdb_id);
            row.querySelector('.list-item-poster').addEventListener('click', open);
            row.querySelector('.list-item-title').addEventListener('click', open);
            row.querySelectorAll('.list-move').forEach(b => b.addEventListener('click', () => {
                const j = i + (+b.dataset.dir);
                if (j < 0 || j >= currentListItems.length) return;
                [currentListItems[i], currentListItems[j]] = [currentListItems[j], currentListItems[i]];
                renderListItems();
                persistOrder();
            }));
            row.querySelector('.list-item-del').addEventListener('click', async () => {
                await fetch(`/lists/${currentListId}/items/${it.tmdb_id}`, { method: 'DELETE' });
                currentListItems.splice(i, 1);
                renderListItems();
            });
            row.addEventListener('dragstart', e => {
                row.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            row.addEventListener('dragend', () => {
                row.classList.remove('dragging');
                // A ordem final é a do DOM (o dragover já moveu o elemento).
                const ids = [...document.getElementById('listItems').children]
                    .map(el => +el.dataset.id);
                currentListItems.sort((a, b) => ids.indexOf(a.tmdb_id) - ids.indexOf(b.tmdb_id));
                renderListItems();
                persistOrder();
            });
            return row;
        }

        // Enquanto arrasta, reposiciona o item na hora conforme o mouse.
        document.getElementById('listItems').addEventListener('dragover', e => {
            e.preventDefault();
            const box = document.getElementById('listItems');
            const dragging = box.querySelector('.list-item.dragging');
            if (!dragging) return;
            const after = [...box.querySelectorAll('.list-item:not(.dragging)')].find(el => {
                const r = el.getBoundingClientRect();
                return e.clientY < r.top + r.height / 2;
            });
            if (after) box.insertBefore(dragging, after); else box.appendChild(dragging);
        });

        async function persistOrder() {
            if (!currentListId) return;
            try {
                await fetch(`/lists/${currentListId}/order`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tmdb_ids: currentListItems.map(x => x.tmdb_id) }),
                });
            } catch (e) { /* melhor-esforço: a ordem local segue valendo na tela */ }
        }

        // Fecha o menu "adicionar a lista" ao clicar fora (o menu é recriado a
        // cada ficha, então a checagem é por id, delegada no documento).
        document.addEventListener('click', e => {
            const menu = document.getElementById('sheetListMenu');
            if (menu && !e.target.closest('.sheet-list-wrap')) menu.style.display = 'none';
        });

        // ========== ABA: ESSENCIAIS (gêneros, estilos, diretores) ==========
        let exploreLoaded = false;
        let exploreOptions = { genres: [], styles: [], directors: [] };
        let exploreMode = 'genres';
        let lastEssentials = null;   // {label, results} — para "salvar como lista"

        async function loadExploreOptions() {
            const status = document.getElementById('exploreStatus');
            try {
                const res = await fetch('/explore/options');
                const data = await res.json();
                if (!res.ok) { status.textContent = data.error || 'Erro ao carregar.'; return; }
                exploreOptions = data;
                renderExploreChips();
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }

        function renderExploreChips() {
            const box = document.getElementById('exploreChips');
            document.getElementById('exploreDirectorWrap').style.display =
                exploreMode === 'directors' ? '' : 'none';
            box.innerHTML = '';
            const mk = (label, small, onclick) => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'explore-chip';
                chip.innerHTML = `<span>${esc(label)}</span>${small ? `<small>${small}</small>` : ''}`;
                chip.addEventListener('click', () => {
                    box.querySelectorAll('.explore-chip').forEach(c => c.classList.remove('on'));
                    chip.classList.add('on');
                    onclick();
                });
                box.appendChild(chip);
            };
            if (exploreMode === 'genres') {
                exploreOptions.genres.forEach(g =>
                    mk(g.name, g.count, () => loadEssentials({ genre: g.name }, g.name)));
            } else if (exploreMode === 'styles') {
                exploreOptions.styles.forEach(s =>
                    mk(s.label, s.count, () => loadEssentials({ style: s.key }, s.label)));
            } else {
                exploreOptions.directors.forEach(d =>
                    mk(d.name, `${d.films} filmes`, () => loadEssentials({ director: d.name }, d.name)));
            }
        }

        document.querySelectorAll('.explore-mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.explore-mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                exploreMode = btn.dataset.mode;
                renderExploreChips();
            });
        });

        async function loadEssentials(params, label) {
            activeRerun = () => loadEssentials(params, label);
            const status = document.getElementById('exploreStatus');
            document.getElementById('exploreActions').style.display = 'none';
            status.textContent = `Montando os essenciais de ${label}...`;
            skeletonGrid('exploreGrid', 12);
            const q = new URLSearchParams({ ...params, n: 100 });
            const sp = streamingParams();
            if (sp) { q.set('region', sp.region); q.set('providers', sp.providers); }
            try {
                const res = await fetch(`/explore/essentials?${q}`);
                const data = await res.json();
                if (!res.ok) { status.textContent = data.error || 'Erro ao montar a lista.'; document.getElementById('exploreGrid').innerHTML = ''; return; }
                lastEssentials = { label, results: data.results };
                status.textContent = `Essenciais: ${label} — ${data.count} filme(s)`
                    + (sp ? ' nos seus streamings' : '');
                renderGrid('exploreGrid', data.results);
                document.getElementById('exploreActions').style.display = '';
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        }

        // Busca livre de diretor (qualquer um do catálogo, ex: Dario Argento).
        (function setupExploreDirectorSearch() {
            const input = document.getElementById('exploreDirectorInput');
            const dropdown = document.getElementById('exploreDirectorDropdown');
            let timer;
            input.addEventListener('input', () => {
                clearTimeout(timer);
                const qs = input.value.trim();
                if (qs.length < 2) { dropdown.style.display = 'none'; return; }
                timer = setTimeout(async () => {
                    try {
                        const people = await (await fetch(
                            `/people?q=${encodeURIComponent(qs)}&role=director`)).json();
                        dropdown.innerHTML = '';
                        if (!people.length) { dropdown.style.display = 'none'; return; }
                        people.forEach(p => {
                            const div = document.createElement('div');
                            div.className = 'autocomplete-item';
                            div.innerHTML = `<span>${esc(p.name)}</span><small>${p.credits} filmes</small>`;
                            div.addEventListener('click', () => {
                                input.value = p.name;
                                dropdown.style.display = 'none';
                                loadEssentials({ director: p.name }, p.name);
                            });
                            dropdown.appendChild(div);
                        });
                        dropdown.style.display = 'block';
                    } catch (e) { dropdown.style.display = 'none'; }
                }, 250);
            });
            document.addEventListener('click', e => {
                if (!input.contains(e.target) && !dropdown.contains(e.target))
                    dropdown.style.display = 'none';
            });
        })();

        // Transforma o ranking exibido numa lista (ordem de assistir = ranking).
        document.getElementById('exploreSaveList').addEventListener('click', async () => {
            if (!lastEssentials) return;
            const status = document.getElementById('exploreStatus');
            status.textContent = 'Salvando como lista...';
            try {
                const res = await fetch('/lists', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: `Essenciais: ${lastEssentials.label}` }),
                });
                const out = await res.json();
                if (!res.ok) { status.textContent = out.error || 'Erro ao criar a lista.'; return; }
                for (const m of lastEssentials.results) {
                    await fetch(`/lists/${out.list.list_id}/items`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tmdb_id: m.tmdb_id, title: m.title,
                                               release_year: m.release_year, poster: m.poster }),
                    });
                }
                status.textContent = `✓ Lista “Essenciais: ${lastEssentials.label}” criada com `
                    + `${lastEssentials.results.length} filmes — veja em Meu › Listas`;
            } catch (e) { status.textContent = 'Erro: ' + e.message; }
        });

        // ========== SKELETON LOADERS ==========
        // Placeholders com shimmer enquanto a busca/recomendação carrega — trocam o
        // "Buscando..." seco por algo que mostra a forma do resultado que vem.
        function skeletonGrid(gridId, n) {
            const grid = document.getElementById(gridId);
            if (!grid) return;
            n = n || 8;
            grid.innerHTML = Array.from({ length: n }, () => (
                '<div class="movie-card skel-card" aria-hidden="true">'
                + '<div class="poster-wrap skeleton"></div>'
                + '<div class="skel-line skeleton" style="width:72%"></div>'
                + '<div class="skel-line skeleton" style="width:44%"></div>'
                + '</div>'
            )).join('');
        }

        // ========== TOUR (coach-marks) ==========
        // Passo a passo destacando elementos reais. 1ª visita dispara sozinho; o
        // botão "?" no cabeçalho reabre. Estado em localStorage.
        const TOUR_KEY = 'cinerd:tour:v1';
        const TOUR_STEPS = [
            { sel: '#searchQuery', tab: 'find', title: 'Comece descrevendo o filme',
              body: 'Uma cena, um clima, um pedaço da história — sem precisar do nome. Ex.: <em>“brinquedos que ganham vida quando ninguém está olhando”</em>.' },
            { sel: '.people-row', tab: 'find', title: 'Sabe quem fez?',
              body: 'Diretor e/ou ator entram aqui e afunilam junto com a descrição. Tudo o que você souber soma.' },
            { sel: '.filters', tab: 'find', title: 'Estreite quando quiser',
              body: 'Gênero, idioma e faixa de anos. Opcionais — use só o que ajudar.' },
            { sel: '[data-menu="descobrir"]', title: 'Já sabe o filme?',
              body: 'Em <strong>Descobrir</strong>: parecidos com um filme que você curtiu, o cânone de um gênero/estilo/diretor, ou um perfil de gosto montado por você.' },
            { sel: '[data-menu="meu"]', title: 'Seu espaço',
              body: 'Com conta, o <strong>Diário</strong> (notas, ❤, resenhas, datas) e as <strong>Listas</strong> com ordem de assistir ficam salvos.' },
            { sel: '#tourBtn', title: 'É isso!',
              body: 'Clique em qualquer pôster para a ficha completa: sinopse, onde assistir e elenco. Este <strong>?</strong> reabre o guia.' },
        ];
        let tourAt = -1, tourEls = null;
        const tourReduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

        function buildTour() {
            if (tourEls) return tourEls;
            const wrap = document.createElement('div');
            wrap.className = 'tour';
            wrap.id = 'tourOverlay';
            wrap.innerHTML =
                '<div class="tour-spot" id="tourSpot"></div>'
                + '<div class="tour-pop" id="tourPop" role="dialog" aria-modal="true" aria-labelledby="tourTitle">'
                + '<h4 id="tourTitle"></h4><p id="tourBody"></p>'
                + '<div class="tour-foot"><span class="tour-count"></span>'
                + '<span class="tour-btns">'
                + '<button type="button" class="tour-skip">Pular</button>'
                + '<button type="button" class="tour-prev">Voltar</button>'
                + '<button type="button" class="tour-next"></button>'
                + '</span></div></div>';
            document.body.appendChild(wrap);
            tourEls = {
                wrap, spot: wrap.querySelector('#tourSpot'), pop: wrap.querySelector('#tourPop'),
                title: wrap.querySelector('#tourTitle'), body: wrap.querySelector('#tourBody'),
                count: wrap.querySelector('.tour-count'), prev: wrap.querySelector('.tour-prev'),
                next: wrap.querySelector('.tour-next'), skip: wrap.querySelector('.tour-skip'),
            };
            tourEls.skip.addEventListener('click', endTour);
            tourEls.prev.addEventListener('click', () => showStep(tourAt - 1));
            tourEls.next.addEventListener('click', () => {
                if (tourAt >= TOUR_STEPS.length - 1) endTour();
                else showStep(tourAt + 1);
            });
            wrap.addEventListener('click', e => { if (e.target === wrap) endTour(); });
            addEventListener('keydown', tourKey);
            addEventListener('resize', tourReposition);
            addEventListener('scroll', tourReposition, true);
            return tourEls;
        }

        function tourKey(e) {
            if (tourAt < 0) return;
            if (e.key === 'Escape') endTour();
            else if (e.key === 'ArrowRight') tourEls.next.click();
            else if (e.key === 'ArrowLeft') showStep(tourAt - 1);
        }

        function tourReposition() {
            if (tourAt < 0) return;
            const step = TOUR_STEPS[tourAt];
            const el = document.querySelector(step.sel);
            const { spot, pop } = tourEls;
            if (!el) { tourEls.wrap.classList.add('no-target'); return; }
            tourEls.wrap.classList.remove('no-target');
            const r = el.getBoundingClientRect();
            const pad = 8;
            spot.style.top = (r.top - pad) + 'px';
            spot.style.left = (r.left - pad) + 'px';
            spot.style.width = (r.width + pad * 2) + 'px';
            spot.style.height = (r.height + pad * 2) + 'px';
            if (matchMedia('(max-width: 560px)').matches) {
                pop.classList.add('sheet');
                pop.style.top = pop.style.left = pop.style.right = '';
                return;
            }
            pop.classList.remove('sheet');
            const pr = pop.getBoundingClientRect();
            let top = r.bottom + 14, left = Math.max(12, Math.min(r.left, innerWidth - pr.width - 12));
            if (top + pr.height > innerHeight - 12) top = Math.max(12, r.top - pr.height - 14);
            pop.style.top = top + 'px';
            pop.style.left = left + 'px';
        }

        function showStep(i) {
            if (i < 0 || i >= TOUR_STEPS.length) return;
            buildTour();
            const step = TOUR_STEPS[i];
            if (step.tab) goTo(step.tab, { silent: true });
            closeNavMenus();
            tourAt = i;
            tourEls.wrap.classList.add('on');
            tourEls.title.innerHTML = step.title;
            tourEls.body.innerHTML = step.body;
            tourEls.count.textContent = (i + 1) + ' / ' + TOUR_STEPS.length;
            tourEls.prev.style.visibility = i === 0 ? 'hidden' : '';
            tourEls.next.textContent = i === TOUR_STEPS.length - 1 ? 'Começar' : 'Próximo';
            const el = document.querySelector(step.sel);
            if (el && !tourReduced) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
            requestAnimationFrame(() => setTimeout(tourReposition, tourReduced ? 0 : 260));
        }

        function endTour() {
            tourAt = -1;
            if (tourEls) tourEls.wrap.classList.remove('on');
            try { localStorage.setItem(TOUR_KEY, 'done'); } catch (e) { /* modo privado */ }
        }

        function startTour(force) {
            let done = false;
            try { done = localStorage.getItem(TOUR_KEY) === 'done'; } catch (e) { /* ignore */ }
            if (done && !force) return;
            // Não abre por cima do fluxo de redefinir senha / confirmar e-mail.
            if (!force && document.getElementById('authModal').style.display === 'flex') return;
            showStep(0);
        }
        document.getElementById('tourBtn').addEventListener('click', () => startTour(true));

        // ========== INIT ==========
        // Crítico primeiro (auth já roda acima); o resto quando o navegador respira.
        const idle = window.requestIdleCallback || (fn => setTimeout(fn, 1200));
        idle(() => { loadGenres(); loadProviders(); });

        if (document.getElementById('tab-pick3').classList.contains('active') && !popularLoaded) {
            popularLoaded = true; loadPopular();
        }
        // 1ª visita: dá um tempo pro layout assentar e abre o guia.
        setTimeout(() => startTour(false), 800);
