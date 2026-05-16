// Dashboard — toggle run form inputs
function toggleRunInputs() {
    const mode = document.getElementById('run-mode').value;
    document.getElementById('manual-inputs').style.display = mode === 'manual_repo_list' ? '' : 'none';
    document.getElementById('niche-inputs').style.display = mode !== 'manual_repo_list' ? '' : 'none';
    if (mode !== 'manual_repo_list') loadNicheCheckboxes();
}

async function loadNicheCheckboxes() {
    const container = document.getElementById('niche-checkboxes');
    if (container.dataset.loaded) return;
    try {
        const resp = await fetch('/api/niches');
        const niches = await resp.json();
        container.innerHTML = niches.map(n =>
            `<label class="checkbox-inline"><input type="checkbox" name="run-niche" value="${n.niche_id}"> ${n.title}</label>`
        ).join('');
        container.dataset.loaded = 'true';
    } catch (e) {
        container.innerHTML = '<span class="text-danger">Failed to load niches</span>';
    }
}

// submitDashboardRun: handles the dashboard New Run form submission
async function submitDashboardRun() {
    const mode = document.getElementById('run-mode').value;
    const label = document.getElementById('run-label').value || null;
    const description = document.getElementById('run-description').value || null;
    const result = document.getElementById('run-result');

    const body = { mode, label, description };

    if (mode === 'manual_repo_list') {
        const raw = document.getElementById('repo-inputs').value.trim();
        if (!raw) { result.textContent = 'Enter at least one repo'; result.className = 'msg error'; return; }
        body.repo_inputs = raw.split('\n').map(s => s.trim()).filter(Boolean);
    } else {
        const checked = [...document.querySelectorAll('input[name="run-niche"]:checked')].map(c => c.value);
        if (!checked.length) { result.textContent = 'Select at least one niche'; result.className = 'msg error'; return; }
        body.niche_ids = checked;
    }

    try {
        const resp = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (resp.ok) {
            result.textContent = `Run #${data.run_id} started`;
            result.className = 'msg success';
            // Trigger immediate refresh of runs section
            const container = document.getElementById('runs-container');
            if (container && typeof htmx !== 'undefined') {
                htmx.ajax('GET', '/partials/dashboard-runs', {target: '#runs-container', swap: 'innerHTML'});
            }
        } else {
            result.textContent = data.detail || 'Failed to create run';
            result.className = 'msg error';
        }
    } catch (e) {
        result.textContent = 'Network error';
        result.className = 'msg error';
    }
}

// removeDashboardRun: removes a run from the Recent Runs list on the dashboard
async function removeDashboardRun(runId) {
    if (!confirm('Remove this run and all its data?')) return;
    const msg = document.getElementById('dashboard-action-msg');
    try {
        const resp = await fetch(`/api/runs/${runId}/remove`, { method: 'POST' });
        if (resp.ok) {
            if (msg) { msg.textContent = 'Run removed.'; msg.className = 'msg success'; }
            // Immediate refresh of runs section
            if (typeof htmx !== 'undefined') {
                htmx.ajax('GET', '/partials/dashboard-runs', {target: '#runs-container', swap: 'innerHTML'});
            }
        } else {
            const data = await resp.json();
            if (msg) { msg.textContent = data.detail || 'Failed to remove'; msg.className = 'msg error'; }
        }
    } catch (e) {
        if (msg) { msg.textContent = 'Network error'; msg.className = 'msg error'; }
    }
}

// Settings
async function saveSettings(e) {
    e.preventDefault();
    const form = document.getElementById('settings-form');
    const result = document.getElementById('settings-result');
    const settings = {};

    form.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        settings[cb.name] = cb.checked ? 'true' : 'false';
    });
    form.querySelectorAll('input[type="number"]').forEach(inp => {
        settings[inp.name] = inp.value;
    });

    try {
        const resp = await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings }),
        });
        if (resp.ok) {
            result.textContent = 'Settings saved';
            result.className = 'msg success';
        } else {
            const data = await resp.json();
            result.textContent = data.detail || 'Failed to save';
            result.className = 'msg error';
        }
    } catch (e) {
        result.textContent = 'Network error';
        result.className = 'msg error';
    }
}

function setRetention(hours) {
    document.querySelector('input[name="log.retention_hours"]').value = hours;
}

function applyFilterPreset(preset) {
    const setVal = (name, val) => {
        const el = document.querySelector(`[name="${name}"]`);
        if (!el) return;
        if (el.type === 'checkbox') el.checked = val === true || val === 'true';
        else el.value = val;
    };
    if (preset === 'permissive') {
        setVal('filter.require_license', false);
        setVal('filter.exclude_forks', false);
        setVal('filter.exclude_archived', true);
        setVal('filter.max_repo_size_kb', 512000);
        setVal('filter.min_stars', 0);
        setVal('filter.min_recent_activity_days', 0);
    } else if (preset === 'standard') {
        setVal('filter.require_license', true);
        setVal('filter.exclude_forks', true);
        setVal('filter.exclude_archived', true);
        setVal('filter.max_repo_size_kb', 102400);
        setVal('filter.min_stars', 5);
        setVal('filter.min_recent_activity_days', 365);
    } else if (preset === 'strict') {
        setVal('filter.require_license', true);
        setVal('filter.exclude_forks', true);
        setVal('filter.exclude_archived', true);
        setVal('filter.max_repo_size_kb', 51200);
        setVal('filter.min_stars', 50);
        setVal('filter.min_recent_activity_days', 180);
    }
}

// Niches
async function toggleNiche(nicheId, enabled) {
    try {
        await fetch('/api/niches', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ niche_id: nicheId, enabled }),
        });
        location.reload();
    } catch (e) {
        alert('Failed to update niche');
    }
}

async function createNiche(e) {
    e.preventDefault();
    const msg = document.getElementById('niche-create-msg');

    const langs = document.getElementById('niche-langs').value.split(',').map(s => s.trim()).filter(Boolean);
    const queries = document.getElementById('niche-queries').value.split('\n').map(s => s.trim()).filter(Boolean);
    const topics = document.getElementById('niche-topics').value.split(',').map(s => s.trim()).filter(Boolean);

    const body = {
        niche_id: document.getElementById('niche-id').value,
        title: document.getElementById('niche-title').value,
        description: document.getElementById('niche-desc').value,
        languages: langs,
        github_search_queries: queries,
        github_topics: topics,
        min_stars: parseInt(document.getElementById('niche-min-stars').value),
        max_repo_size_kb: parseInt(document.getElementById('niche-max-size').value),
        min_recent_activity_days: parseInt(document.getElementById('niche-min-activity').value),
        exclude_forks: document.getElementById('niche-exclude-forks').checked,
        enabled: document.getElementById('niche-enabled').checked,
    };

    if (!body.niche_id || !body.title) {
        msg.textContent = 'Niche ID and Title are required';
        msg.className = 'msg error';
        return;
    }
    if (!queries.length) {
        msg.textContent = 'At least one search query is required';
        msg.className = 'msg error';
        return;
    }

    try {
        const resp = await fetch('/api/niches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            msg.textContent = 'Niche created! Reloading...';
            msg.className = 'msg success';
            setTimeout(() => location.reload(), 500);
        } else {
            const data = await resp.json();
            msg.textContent = data.detail || 'Failed to create niche';
            msg.className = 'msg error';
        }
    } catch (e) {
        msg.textContent = 'Network error';
        msg.className = 'msg error';
    }
}

// Schedules
async function createSchedule(e) {
    e.preventDefault();
    const result = document.getElementById('sched-result');
    const nicheIds = [...document.querySelectorAll('input[name="sched-niches"]:checked')].map(c => c.value);

    const body = {
        name: document.getElementById('sched-name').value,
        cron_expression: document.getElementById('sched-cron').value,
        niche_ids: nicheIds,
        enabled: document.getElementById('sched-enabled').checked,
    };

    try {
        const resp = await fetch('/api/schedules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            result.textContent = 'Schedule created';
            result.className = 'msg success';
            setTimeout(() => location.reload(), 500);
        } else {
            const data = await resp.json();
            result.textContent = data.detail || 'Failed';
            result.className = 'msg error';
        }
    } catch (e) {
        result.textContent = 'Network error';
        result.className = 'msg error';
    }
}

async function toggleSchedule(id, enabled) {
    try {
        await fetch(`/api/schedules/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        location.reload();
    } catch (e) {
        alert('Failed to update schedule');
    }
}

async function deleteSchedule(id) {
    if (!confirm('Delete this schedule?')) return;
    try {
        await fetch(`/api/schedules/${id}`, { method: 'DELETE' });
        location.reload();
    } catch (e) {
        alert('Failed to delete schedule');
    }
}

// GitHub diagnostics
async function checkGitHub() {
    const panel = document.getElementById('github-status');
    panel.innerHTML = '<p class="muted">Checking...</p>';
    try {
        const resp = await fetch('/api/github/status');
        const data = await resp.json();
        panel.innerHTML = `<table>
            <tr><td>Reachable</td><td>${data.reachable ? 'Yes' : 'No'}</td></tr>
            <tr><td>Auth Mode</td><td>${data.auth_mode}</td></tr>
            <tr><td>Authenticated</td><td>${data.authenticated ? 'Yes' : 'No'}</td></tr>
            <tr><td>Login</td><td>${data.login || '-'}</td></tr>
            <tr><td>Rate Limit</td><td>${data.rate_limit.remaining} / ${data.rate_limit.limit}</td></tr>
            <tr><td>Reset At</td><td>${data.rate_limit.reset_at ? new Date(data.rate_limit.reset_at * 1000).toLocaleString() : '-'}</td></tr>
            ${data.error ? `<tr><td>Error</td><td class="text-danger">${data.error}</td></tr>` : ''}
        </table>`;
    } catch (e) {
        panel.innerHTML = '<p class="text-danger">Failed to check GitHub</p>';
    }
}

// Search
async function searchRepos() {
    const q = document.getElementById('repo-search').value.trim();
    const resultsDiv = document.getElementById('repo-results');
    if (!q || q.length < 2) {
        resultsDiv.innerHTML = '';
        return;
    }
    try {
        const resp = await fetch(`/api/search/repos?q=${encodeURIComponent(q)}`);
        const repos = await resp.json();
        if (!repos.length) {
            resultsDiv.innerHTML = '<p class="muted">No repos found</p>';
            return;
        }
        resultsDiv.innerHTML = repos.map(r => `<div class="search-result">
            <a href="${r.source_url}" target="_blank">${r.full_name}</a>
            <span class="badge">⭐ ${r.stars}</span>
            <button class="btn btn-sm" onclick="quickCloneRepo('${r.full_name}')">Clone</button>
        </div>`).join('');
    } catch (e) {
        resultsDiv.innerHTML = '<p class="text-danger">Search failed</p>';
    }
}

async function searchNiches() {
    const q = document.getElementById('niche-search').value.trim();
    const resultsDiv = document.getElementById('niche-results');
    if (!q || q.length < 2) {
        resultsDiv.innerHTML = '';
        return;
    }
    try {
        const resp = await fetch(`/api/search/niches?q=${encodeURIComponent(q)}`);
        const niches = await resp.json();
        if (!niches.length) {
            resultsDiv.innerHTML = '<p class="muted">No niches found</p>';
            return;
        }
        resultsDiv.innerHTML = niches.map(n => `<div class="search-result">
            <strong>${n.title}</strong> <code>${n.niche_id}</code>
            <p class="muted">${n.description}</p>
            <button class="btn btn-sm" onclick="quickRunNiche('${n.niche_id}')">Run this niche</button>
            <span class="badge">${n.enabled ? 'Enabled' : 'Disabled'}</span>
        </div>`).join('');
    } catch (e) {
        resultsDiv.innerHTML = '<p class="text-danger">Search failed</p>';
    }
}

async function quickCloneRepo(fullName) {
    const result = await submitRun({
        mode: 'manual_repo_list',
        repo_inputs: [fullName],
        label: `Quick clone: ${fullName}`
    });
    if (result) alert('Run started!');
}

async function quickRunNiche(nicheId) {
    const result = await submitRun({
        mode: 'niche_group',
        niche_ids: [nicheId],
        label: `Quick niche run: ${nicheId}`
    });
    if (result) alert('Run started!');
}

async function submitRun(body) {
    try {
        const resp = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            const data = await resp.json();
            location.href = `/?run=${data.run_id}`;
            return true;
        }
    } catch (e) {}
    return false;
}

// Run controls
async function retryRun(runId) {
    const msg = document.getElementById('action-msg');
    try {
        const resp = await fetch(`/api/runs/${runId}/retry`, { method: 'POST' });
        if (resp.ok) {
            const data = await resp.json();
            msg.textContent = `Retrying as run #${data.new_run_id}...`;
            msg.className = 'msg success';
            setTimeout(() => location.href = `/?run=${data.new_run_id}`, 500);
        } else {
            const data = await resp.json();
            msg.textContent = data.detail || 'Failed to retry';
            msg.className = 'msg error';
        }
    } catch (e) {
        msg.textContent = 'Network error';
        msg.className = 'msg error';
    }
}

async function removeRun(runId) {
    if (!confirm('Remove this run and all its data?')) return;
    const msg = document.getElementById('action-msg');
    try {
        const resp = await fetch(`/api/runs/${runId}/remove`, { method: 'POST' });
        if (resp.ok) {
            msg.textContent = 'Run removed. Redirecting...';
            msg.className = 'msg success';
            setTimeout(() => location.href = '/', 1000);
        } else {
            const data = await resp.json();
            msg.textContent = data.detail || 'Failed to remove';
            msg.className = 'msg error';
        }
    } catch (e) {
        msg.textContent = 'Network error';
        msg.className = 'msg error';
    }
}

// --- GitHub Discovery & Cart ---

// Store discover results so onclick handlers reference by index, never inline JSON.
window._discoverResults = [];

async function discoverGitHub() {
    const q = (document.getElementById('discover-query')?.value || '').trim();
    const lang = (document.getElementById('discover-lang')?.value || '').trim();
    const stars = parseInt(document.getElementById('discover-stars')?.value || '0', 10) || 0;
    const statusEl = document.getElementById('discover-status');
    const resultsEl = document.getElementById('discover-results');

    if (!q || q.length < 2) {
        statusEl.textContent = 'Enter at least 2 characters.';
        statusEl.className = 'msg error';
        return;
    }

    statusEl.textContent = 'Searching GitHub\u2026';
    statusEl.className = 'msg';
    resultsEl.textContent = '';
    window._discoverResults = [];

    try {
        const params = new URLSearchParams({ q });
        if (lang) params.set('language', lang);
        if (stars > 0) params.set('min_stars', stars);

        const resp = await fetch('/api/discover?' + params);
        if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
        const repos = await resp.json();

        if (!repos.length) {
            statusEl.textContent = 'No results found.';
            statusEl.className = 'msg';
            return;
        }

        statusEl.textContent = repos.length + ' result' + (repos.length !== 1 ? 's' : '');
        statusEl.className = 'msg success';
        window._discoverResults = repos;

        // Build result cards using only safe DOM methods — no untrusted innerHTML.
        resultsEl.textContent = '';
        repos.forEach(function(r, idx) {
            const card = document.createElement('div');
            card.className = 'search-result';
            card.style.cssText = 'border-left:3px solid ' + (r.filter_accepted ? '#4caf50' : '#f44336') + ';padding-left:0.75rem;margin-bottom:0.75rem;';

            const header = document.createElement('div');
            header.style.cssText = 'display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem;flex-wrap:wrap;';

            const meta = document.createElement('div');

            const link = document.createElement('a');
            link.href = r.source_url || ('https://github.com/' + r.full_name);
            link.target = '_blank';
            link.rel = 'noopener';
            const strong = document.createElement('strong');
            strong.textContent = r.full_name;
            link.appendChild(strong);
            meta.appendChild(link);

            function addBadge(text, cls) {
                const b = document.createElement('span');
                b.className = 'badge ' + (cls || '');
                b.textContent = ' ' + text;
                meta.appendChild(b);
            }

            const lang0 = r.languages && r.languages[0];
            if (lang0) addBadge(lang0, '');

            if (r.quality_score !== null && r.quality_score !== undefined) {
                const pct = Math.round(r.quality_score * 100);
                const qcls = r.quality_score >= 0.7 ? 'badge-success' : r.quality_score >= 0.4 ? 'badge-warn' : 'badge-danger';
                addBadge('Q: ' + pct + '%', qcls);
            }
            if (r.filter_accepted) {
                addBadge('passes filters', 'badge-success');
            } else {
                addBadge(r.filter_reason || 'filtered', 'badge-danger');
            }
            if (r.is_fork) addBadge('fork', 'badge-warn');
            if (r.is_archived) addBadge('archived', 'badge-warn');
            if (!r.license) addBadge('no license', 'badge-danger');

            const btn = document.createElement('button');
            btn.className = 'btn btn-sm btn-primary';
            btn.textContent = '+ Cart';
            btn.onclick = function() { addToCartByIndex(idx); };

            header.appendChild(meta);
            header.appendChild(btn);
            card.appendChild(header);

            const details = document.createElement('div');
            details.style.cssText = 'margin-top:0.25rem;font-size:0.85em;color:var(--muted,#aaa);';
            details.textContent = '\u2b50 ' + (r.stars || 0) + '  \u2022  ' + (r.license || 'no license') + '  \u2022  ' + (r.size_kb || 0) + ' KB';
            card.appendChild(details);

            if (r.description) {
                const desc = document.createElement('p');
                desc.className = 'muted';
                desc.style.margin = '0.25rem 0 0';
                desc.textContent = r.description.slice(0, 120) + (r.description.length > 120 ? '\u2026' : '');
                card.appendChild(desc);
            }

            resultsEl.appendChild(card);
        });
    } catch (e) {
        statusEl.textContent = 'Search failed: ' + e.message;
        statusEl.className = 'msg error';
    }
}

async function addToCartByIndex(idx) {
    const repo = window._discoverResults[idx];
    if (!repo) return;
    const body = {
        full_name: repo.full_name,
        source_url: repo.source_url || '',
        owner: repo.owner || '',
        name: repo.name || '',
        language: (repo.languages && repo.languages[0]) || null,
        stars: repo.stars || 0,
        size_kb: repo.size_kb || 0,
        license: repo.license || null,
        topics: repo.topics || [],
        description: repo.description || '',
        last_pushed_at: repo.last_pushed_at || null,
        quality_score: (repo.quality_score !== undefined ? repo.quality_score : null),
        is_fork: repo.is_fork || false,
        is_archived: repo.is_archived || false,
    };
    const statusEl = document.getElementById('discover-status');
    try {
        const resp = await fetch('/api/cart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (resp.ok) {
            statusEl.textContent = data.already_in_cart
                ? (repo.full_name + ' is already in your cart.')
                : ('Added ' + repo.full_name + ' to cart.');
            statusEl.className = 'msg success';
        } else {
            statusEl.textContent = data.detail || 'Failed to add to cart';
            statusEl.className = 'msg error';
        }
    } catch (e) {
        statusEl.textContent = 'Network error';
        statusEl.className = 'msg error';
    }
}

async function removeFromCart(itemId) {
    const msg = document.getElementById('cart-action-msg');
    try {
        const resp = await fetch('/api/cart/' + itemId, { method: 'DELETE' });
        if (resp.ok) {
            location.reload();
        } else {
            const data = await resp.json();
            if (msg) { msg.textContent = data.detail || 'Failed to remove'; msg.className = 'msg error'; }
        }
    } catch (e) {
        if (msg) { msg.textContent = 'Network error'; msg.className = 'msg error'; }
    }
}

async function clearCart() {
    if (!confirm('Remove all repos from the cart?')) return;
    const msg = document.getElementById('cart-action-msg');
    try {
        const resp = await fetch('/api/cart', { method: 'DELETE' });
        if (resp.ok) {
            location.reload();
        } else {
            const data = await resp.json();
            if (msg) { msg.textContent = data.detail || 'Failed to clear cart'; msg.className = 'msg error'; }
        }
    } catch (e) {
        if (msg) { msg.textContent = 'Network error'; msg.className = 'msg error'; }
    }
}

async function cloneFromCart() {
    if (!confirm('Start cloning all repos in the cart?')) return;
    const msg = document.getElementById('cart-action-msg');
    try {
        const resp = await fetch('/api/cart/clone', { method: 'POST' });
        const data = await resp.json();
        if (resp.ok) {
            if (msg) { msg.textContent = 'Clone run #' + data.run_id + ' started (' + data.repo_count + ' repos). Redirecting\u2026'; msg.className = 'msg success'; }
            setTimeout(function() { location.href = '/?run=' + data.run_id; }, 800);
        } else {
            if (msg) { msg.textContent = data.detail || 'Failed to start clone'; msg.className = 'msg error'; }
        }
    } catch (e) {
        if (msg) { msg.textContent = 'Network error'; msg.className = 'msg error'; }
    }
}
