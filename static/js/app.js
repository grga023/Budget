const API = "/api";

let config = { currency: "RSD", categories: [], subcategories: {} };
let users = [];
let currentUserId = null;
let currentAccountId = null; // null = all accounts for the current user
let transactions = [];
let currentType = "expense";
let currentFilter = "all";
let categoryChart = null;
let monthChart = null;
let dayChart = null;
// First day of the analytics period (a Date). null = current month.
let analyticsMonth = null;

let fmt = (n) =>
    new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: config.currency,
        maximumFractionDigits: 2,
    }).format(n);

const PALETTE = [
    "#6c63ff", "#ff5f6d", "#2ecc8f", "#ffb142", "#48dbfb",
    "#ff9ff3", "#54a0ff", "#5f27cd", "#1dd1a1", "#feca57", "#a29bfe",
];

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : str;
    return div.innerHTML;
}

async function api(path, options) {
    const res = await fetch(API + path, options);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Request failed (${res.status})`);
    }
    return res.status === 204 ? null : res.json();
}

// ----------------------------------------------------------------- load
async function loadConfig() {
    config = await api("/config");
    fmt = (n) =>
        new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: config.currency,
            maximumFractionDigits: 2,
        }).format(n);
}

async function loadUsers() {
    users = await api("/users");
    if (currentUserId === null && users.length) currentUserId = users[0].id;
    if (!users.find((u) => u.id === currentUserId) && users.length)
        currentUserId = users[0].id;
    // Drop a stale account selection that no longer belongs to this user.
    const u = currentUser();
    if (currentAccountId !== null && (!u || !u.accounts.some((a) => a.id === currentAccountId)))
        currentAccountId = null;
    renderUserSwitch();
    renderAccountBar();
    renderTxAccountSelect();
    renderTransferTargets();
}

async function loadTransactions() {
    if (currentUserId === null) {
        transactions = [];
    } else if (currentAccountId !== null) {
        transactions = await api(`/transactions?account_id=${currentAccountId}`);
    } else {
        transactions = await api(`/transactions?user_id=${currentUserId}`);
    }
    renderSummary();
    renderList();
}

// ----------------------------------------------------------------- render
function renderUserSwitch() {
    const wrap = document.getElementById("userSwitch");
    wrap.innerHTML = "";
    for (const u of users) {
        const btn = document.createElement("button");
        btn.className = "user-btn" + (u.id === currentUserId ? " active" : "");
        btn.innerHTML = `<span class="user-name">${escapeHtml(u.name)}</span>
            <span class="user-balance">${fmt(u.balance)}</span>`;
        btn.addEventListener("click", () => {
            currentUserId = u.id;
            currentAccountId = null;
            renderUserSwitch();
            renderAccountBar();
            renderTxAccountSelect();
            renderTransferTargets();
            loadTransactions();
        });
        wrap.appendChild(btn);
    }
}

function currentUser() {
    return users.find((u) => u.id === currentUserId);
}

function currentAccount() {
    const u = currentUser();
    if (!u || currentAccountId === null) return null;
    return u.accounts.find((a) => a.id === currentAccountId) || null;
}

const ACCOUNT_ICON = { checking: "💳", savings: "🏦", loan: "📉", cash: "💵" };

function renderAccountBar() {
    const wrap = document.getElementById("accountBar");
    if (!wrap) return;
    const u = currentUser();
    wrap.innerHTML = "";
    if (!u) return;

    const allBtn = document.createElement("button");
    allBtn.className = "acct-chip" + (currentAccountId === null ? " active" : "");
    allBtn.innerHTML = `<span class="acct-name">All accounts</span>
        <span class="acct-balance">${fmt(u.balance)}</span>`;
    allBtn.addEventListener("click", () => selectAccount(null));
    wrap.appendChild(allBtn);

    for (const a of u.accounts) {
        const btn = document.createElement("button");
        btn.className = "acct-chip" + (a.id === currentAccountId ? " active" : "");
        const icon = ACCOUNT_ICON[a.type] || "💳";
        btn.innerHTML = `<span class="acct-name">${icon} ${escapeHtml(a.name)}</span>
            <span class="acct-balance ${a.balance < 0 ? "neg" : ""}">${fmt(a.balance)}</span>`;
        btn.addEventListener("click", () => selectAccount(a.id));
        wrap.appendChild(btn);
    }
}

function selectAccount(id) {
    currentAccountId = id;
    renderAccountBar();
    loadTransactions();
}

function renderSummary() {
    const u = currentUser();
    if (!u) return;
    const a = currentAccount();
    const src = a || u;
    document.getElementById("balanceValue").textContent = fmt(src.balance);
    document.getElementById("incomeValue").textContent = fmt(src.income);
    document.getElementById("expenseValue").textContent = fmt(src.expense);
    document.getElementById("initialSub").textContent =
        `Initial ${fmt(src.initial_balance)}`;
}

function renderTxAccountSelect() {
    const sel = document.getElementById("txAccount");
    if (!sel) return;
    const u = currentUser();
    if (!u) { sel.innerHTML = ""; return; }
    const prefer = currentAccountId !== null ? currentAccountId
        : (u.accounts[0] ? u.accounts[0].id : null);
    sel.innerHTML = u.accounts
        .map((a) => `<option value="${a.id}" ${a.id === prefer ? "selected" : ""}>${escapeHtml(a.name)}</option>`)
        .join("");
}

function categoryOptions(selected, kind) {
    const list = kind === "income" ? config.income_categories : config.categories;
    return (list || [])
        .map(
            (c) =>
                `<option value="${escapeHtml(c)}" ${c === selected ? "selected" : ""}>${escapeHtml(c)}</option>`
        )
        .join("");
}

function renderCategorySelect() {
    const sel = document.getElementById("category");
    if (currentType === "income") {
        sel.innerHTML = categoryOptions(null, "income");
        document.getElementById("catHint").style.display = "none";
    } else {
        sel.innerHTML = `<option value="">Auto-detect</option>` + categoryOptions(null, "expense");
        document.getElementById("catHint").style.display = "block";
    }
}

function renderList() {
    const list = document.getElementById("txList");
    const empty = document.getElementById("emptyState");
    list.innerHTML = "";

    const filtered = transactions.filter(
        (t) => currentFilter === "all" || t.type === currentFilter
    );
    empty.style.display = filtered.length ? "none" : "block";

    for (const t of filtered) {
        const li = document.createElement("li");
        li.className = "tx-item";
        const sign = t.type === "income" ? "+" : "−";
        const title = t.merchant || t.category;
        const sub = t.subcategory ? ` · ${escapeHtml(t.subcategory)}` : "";
        const srcBadge = t.source && t.source !== "manual"
            ? `<span class="badge">${escapeHtml(t.source)}</span>` : "";

        const catControl =
            `<select class="tx-cat-select" data-id="${t.id}">${categoryOptions(t.category, t.type)}</select>`;

        li.innerHTML = `
            <div class="tx-icon ${t.type}">${t.type === "income" ? "↑" : "↓"}</div>
            <div class="tx-info">
                <div class="tx-title">${escapeHtml(title)}${srcBadge}</div>
                <div class="tx-meta">${t.date}${sub}${t.note ? " · " + escapeHtml(t.note) : ""}</div>
            </div>
            <div class="tx-right">
                ${catControl}
                <span class="tx-amount ${t.type}">${sign}${fmt(t.amount)}</span>
                <button class="tx-delete" data-id="${t.id}" title="Delete">×</button>
            </div>`;
        list.appendChild(li);
    }

    list.querySelectorAll(".tx-delete").forEach((b) =>
        b.addEventListener("click", () => deleteTransaction(Number(b.dataset.id)))
    );
    list.querySelectorAll(".tx-cat-select").forEach((s) =>
        s.addEventListener("change", () =>
            updateTransaction(Number(s.dataset.id), { category: s.value })
        )
    );
}

function renderTransferTargets() {
    const u = currentUser();
    const fromSel = document.getElementById("transferFrom");
    const toSel = document.getElementById("transferTo");
    if (!u) { fromSel.innerHTML = ""; toSel.innerHTML = ""; return; }

    // From: the current user's own accounts.
    fromSel.innerHTML = u.accounts
        .map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`)
        .join("");

    // To: every account, grouped by owner; default to a different one.
    toSel.innerHTML = users
        .map((usr) => {
            const opts = usr.accounts
                .map((a) => `<option value="${a.id}">${escapeHtml(usr.name)} · ${escapeHtml(a.name)}</option>`)
                .join("");
            return `<optgroup label="${escapeHtml(usr.name)}">${opts}</optgroup>`;
        })
        .join("");
    // Preselect the first account that isn't the default "from".
    const fromId = u.accounts[0] ? String(u.accounts[0].id) : "";
    for (const opt of toSel.options) {
        if (opt.value !== fromId) { opt.selected = true; break; }
    }
}

// ----------------------------------------------------------------- actions
async function addTransaction(payload) {
    try {
        await api("/transactions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        await refreshAll();
    } catch (e) {
        alert(e.message);
    }
}

async function updateTransaction(id, payload) {
    try {
        await api(`/transactions/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        await refreshAll();
    } catch (e) {
        alert(e.message);
    }
}

async function deleteTransaction(id) {
    await api(`/transactions/${id}`, { method: "DELETE" });
    await refreshAll();
}

async function refreshAll() {
    await loadUsers();
    await loadTransactions();
    if (!document.getElementById("view-analytics").classList.contains("hidden"))
        loadAnalytics();
    if (!document.getElementById("view-settings").classList.contains("hidden"))
        renderSettings();
}

// ----------------------------------------------------------------- analytics
async function loadAnalytics() {
    if (currentUserId === null) return;
    let url = currentAccountId !== null
        ? `/analytics?account_id=${currentAccountId}`
        : `/analytics?user_id=${currentUserId}`;
    if (analyticsMonth) {
        const m = `${analyticsMonth.getFullYear()}-${String(analyticsMonth.getMonth() + 1).padStart(2, "0")}`;
        url += `&month=${m}`;
    }
    const a = await api(url);

    document.getElementById("periodLabel").textContent = a.period.label;

    document.getElementById("statRow").innerHTML = `
        <div class="stat"><div class="label">Spending</div><div class="value">${fmt(a.spending)}</div></div>
        <div class="stat"><div class="label">Net</div><div class="value">${fmt(a.net)}</div></div>
        <div class="stat"><div class="label">Avg / tx</div><div class="value">${fmt(a.avg_transaction)}</div></div>
        <div class="stat"><div class="label">Top category</div><div class="value">${a.top_category || "—"}</div></div>`;

    renderCategoryChart(a.by_category);
    renderDayChart(a.by_day);
    renderMonthChart(a.by_month);

    document.getElementById("merchantList").innerHTML =
        a.top_merchants.length
            ? a.top_merchants
                  .map(
                      (m) =>
                          `<li><span>${escapeHtml(m.merchant)}</span><strong>${fmt(m.amount)}</strong></li>`
                  )
                  .join("")
            : `<li class="hint">No merchant data yet.</li>`;

    document.getElementById("subcategoryList").innerHTML =
        a.by_subcategory.length
            ? a.by_subcategory
                  .map(
                      (s) =>
                          `<li><span>${escapeHtml(s.name)}</span><strong>${fmt(s.amount)}</strong></li>`
                  )
                  .join("")
            : `<li class="hint">No spending in this period.</li>`;
}

function renderDayChart(byDay) {
    const canvas = document.getElementById("dayChart");
    if (dayChart) dayChart.destroy();
    dayChart = new Chart(canvas, {
        type: "bar",
        data: {
            labels: byDay.map((d) => Number(d.date.slice(8, 10))),
            datasets: [{ label: "Spent", data: byDay.map((d) => d.expense), backgroundColor: "#6c63ff", borderRadius: 4 }],
        },
        options: {
            plugins: { legend: { display: false } },
            scales: {
                x: { ticks: { color: "#8b91a3", maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } },
                y: { ticks: { color: "#8b91a3" }, grid: { color: "#2a2f3c" } },
            },
        },
    });
}

function renderCategoryChart(byCat) {
    const canvas = document.getElementById("categoryChart");
    const empty = document.getElementById("catEmpty");
    const legend = document.getElementById("catLegend");

    if (!byCat.length) {
        canvas.style.display = "none";
        empty.style.display = "block";
        legend.innerHTML = "";
        if (categoryChart) { categoryChart.destroy(); categoryChart = null; }
        return;
    }
    canvas.style.display = "block";
    empty.style.display = "none";

    const labels = byCat.map((c) => c.category);
    const data = byCat.map((c) => c.amount);
    if (categoryChart) categoryChart.destroy();
    categoryChart = new Chart(canvas, {
        type: "doughnut",
        data: { labels, datasets: [{ data, backgroundColor: PALETTE, borderColor: "#181b24", borderWidth: 2 }] },
        options: { plugins: { legend: { display: false } }, cutout: "62%" },
    });

    legend.innerHTML = byCat
        .map(
            (c, i) =>
                `<li><span><span class="dot" style="background:${PALETTE[i % PALETTE.length]}"></span>${escapeHtml(c.category)}<span class="pct">${c.percent}%</span></span><strong>${fmt(c.amount)}</strong></li>`
        )
        .join("");
}

function renderMonthChart(byMonth) {
    const canvas = document.getElementById("monthChart");
    const labels = byMonth.map((m) => m.month);
    if (monthChart) monthChart.destroy();
    monthChart = new Chart(canvas, {
        type: "bar",
        data: {
            labels,
            datasets: [
                { label: "Income", data: byMonth.map((m) => m.income), backgroundColor: "#2ecc8f" },
                { label: "Expense", data: byMonth.map((m) => m.expense), backgroundColor: "#ff5f6d" },
            ],
        },
        options: {
            plugins: { legend: { labels: { color: "#8b91a3" } } },
            scales: {
                x: { ticks: { color: "#8b91a3" }, grid: { display: false } },
                y: { ticks: { color: "#8b91a3" }, grid: { color: "#2a2f3c" } },
            },
        },
    });
}

// ----------------------------------------------------------------- settings
function renderSettings() {
    renderUserSettings();
    renderCategoryChips();
    renderRules();
    renderWalletExample();
}

const ACCOUNT_TYPES = ["checking", "savings", "loan", "cash"];

function accountTypeOptions(selected) {
    return ACCOUNT_TYPES
        .map((t) => `<option value="${t}" ${t === selected ? "selected" : ""}>${t[0].toUpperCase() + t.slice(1)}</option>`)
        .join("");
}

async function renderUserSettings() {
    const wrap = document.getElementById("userSettings");
    wrap.innerHTML = "";
    for (const u of users) {
        const cards = await api(`/users/${u.id}/cards`);
        const acctOpts = (sel) => u.accounts
            .map((a) => `<option value="${a.id}" ${a.id === sel ? "selected" : ""}>${escapeHtml(a.name)}</option>`)
            .join("");
        const box = document.createElement("div");
        box.className = "user-config";
        box.innerHTML = `
            <div class="uc-head">
                <input type="text" value="${escapeHtml(u.name)}" data-field="name" />
            </div>
            <div class="uc-section-label">Accounts</div>
            <div class="uc-accounts">
                ${u.accounts
                    .map(
                        (a) =>
                            `<div class="account-row" data-account="${a.id}">
                                <input type="text" class="ar-name" value="${escapeHtml(a.name)}" aria-label="Account name" />
                                <select class="ar-type">${accountTypeOptions(a.type)}</select>
                                <input type="number" step="0.01" class="ar-initial" value="${a.initial_balance}" title="Initial balance" />
                                <span class="ar-balance ${a.balance < 0 ? "neg" : ""}">${fmt(a.balance)}</span>
                                <button class="ar-save tiny-btn" title="Save account">✓</button>
                                ${u.accounts.length > 1 ? `<button class="ar-del" title="Delete account">×</button>` : ""}
                            </div>`
                    )
                    .join("")}
            </div>
            <div class="add-account">
                <input type="text" class="new-acct-name" placeholder="Account name (e.g. Savings)" />
                <select class="new-acct-type">${accountTypeOptions("savings")}</select>
                <input type="number" step="0.01" class="new-acct-initial" placeholder="Initial" />
                <button class="tiny-btn add-account-btn">Add account</button>
            </div>
            <div class="uc-section-label">Cards</div>
            <div class="uc-cards">
                ${cards
                    .map(
                        (c) =>
                            `<div class="card-row">
                                <input type="text" class="cr-label-input" value="${escapeHtml(c.label)}" data-card="${c.id}" aria-label="Card name" />
                                <span class="cr-id">${escapeHtml(c.identifier)}</span>
                                <select class="cr-account" data-card="${c.id}" title="Linked account">${acctOpts(c.account_id)}</select>
                                <button class="cr-del" data-card="${c.id}" title="Remove card">×</button>
                            </div>`
                    )
                    .join("")}
            </div>
            <div class="add-card">
                <input type="text" class="new-card-label" placeholder="Card name (e.g. Visa)" />
                <input type="text" class="new-card-id" placeholder="Card id / token" />
                <select class="new-card-account">${acctOpts(u.accounts[0] ? u.accounts[0].id : null)}</select>
                <button class="tiny-btn add-card-btn">Add card</button>
            </div>
            <div class="uc-actions">
                <button class="ghost-btn save-user">Save name</button>
                ${users.length > 1 ? `<button class="tiny-btn del-user">Delete user</button>` : ""}
            </div>`;

        box.querySelector(".save-user").addEventListener("click", async () => {
            const name = box.querySelector('[data-field="name"]').value;
            try {
                await api(`/users/${u.id}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name }),
                });
                await refreshAll();
            } catch (e) { alert(e.message); }
        });

        // --- account add / edit / delete ---
        box.querySelector(".add-account-btn").addEventListener("click", async () => {
            const name = box.querySelector(".new-acct-name").value.trim();
            if (!name) return alert("Account name is required");
            const type = box.querySelector(".new-acct-type").value;
            const initial_balance = box.querySelector(".new-acct-initial").value || 0;
            try {
                await api(`/users/${u.id}/accounts`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name, type, initial_balance }),
                });
                await refreshAll();
            } catch (e) { alert(e.message); }
        });

        box.querySelectorAll(".account-row").forEach((row) => {
            const id = Number(row.dataset.account);
            const saveBtn = row.querySelector(".ar-save");
            if (saveBtn)
                saveBtn.addEventListener("click", async () => {
                    try {
                        await api(`/accounts/${id}`, {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                name: row.querySelector(".ar-name").value,
                                type: row.querySelector(".ar-type").value,
                                initial_balance: row.querySelector(".ar-initial").value,
                            }),
                        });
                        await refreshAll();
                    } catch (e) { alert(e.message); }
                });
            const delBtn = row.querySelector(".ar-del");
            if (delBtn)
                delBtn.addEventListener("click", async () => {
                    if (!confirm("Delete this account and its transactions?")) return;
                    try {
                        await api(`/accounts/${id}`, { method: "DELETE" });
                        if (currentAccountId === id) currentAccountId = null;
                        await refreshAll();
                    } catch (e) { alert(e.message); }
                });
        });

        box.querySelectorAll(".cr-account").forEach((sel) =>
            sel.addEventListener("change", async () => {
                try {
                    await api(`/cards/${sel.dataset.card}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ account_id: Number(sel.value) }),
                    });
                } catch (e) { alert(e.message); }
            })
        );

        const delUser = box.querySelector(".del-user");
        if (delUser)
            delUser.addEventListener("click", async () => {
                if (!confirm(`Delete ${u.name} and all their transactions?`)) return;
                await api(`/users/${u.id}`, { method: "DELETE" });
                if (currentUserId === u.id) currentUserId = null;
                await refreshAll();
            });

        box.querySelector(".add-card-btn").addEventListener("click", async () => {
            const label = box.querySelector(".new-card-label").value.trim() || "Card";
            const identifier = box.querySelector(".new-card-id").value.trim();
            if (!identifier) return alert("Card id is required");
            const account_id = Number(box.querySelector(".new-card-account").value) || null;
            try {
                await api(`/users/${u.id}/cards`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ label, identifier, account_id }),
                });
                renderUserSettings();
            } catch (e) { alert(e.message); }
        });

        box.querySelectorAll(".cr-del").forEach((b) =>
            b.addEventListener("click", async () => {
                await api(`/cards/${b.dataset.card}`, { method: "DELETE" });
                renderUserSettings();
            })
        );

        box.querySelectorAll(".cr-label-input").forEach((inp) => {
            const save = async () => {
                const label = inp.value.trim();
                if (!label || label === inp.defaultValue) return;
                try {
                    await api(`/cards/${inp.dataset.card}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ label }),
                    });
                    inp.defaultValue = label;
                } catch (e) { alert(e.message); }
            };
            inp.addEventListener("blur", save);
            inp.addEventListener("keydown", (e) => {
                if (e.key === "Enter") inp.blur();
            });
        });

        wrap.appendChild(box);
    }
}

function renderCategoryChips() {
    const ul = document.getElementById("categoryChips");
    const chip = (c, kind) =>
        `<li class="chip ${kind}">${escapeHtml(c)}<button data-cat="${escapeHtml(c)}" title="Remove">×</button></li>`;
    ul.innerHTML =
        `<li class="chip-group">Expenses</li>` +
        config.categories.map((c) => chip(c, "exp")).join("") +
        `<li class="chip-group">Income</li>` +
        (config.income_categories || []).map((c) => chip(c, "inc")).join("");
    ul.querySelectorAll("button").forEach((b) =>
        b.addEventListener("click", async () => {
            await api(`/categories/${encodeURIComponent(b.dataset.cat)}`, { method: "DELETE" });
            await loadConfig();
            renderCategorySelect();
            renderCategoryChips();
            renderList();
        })
    );
}

async function renderRules() {
    const ul = document.getElementById("ruleList");
    const rules = await api("/rules");
    ul.innerHTML = rules.length
        ? rules
              .map(
                  (r) =>
                      `<li class="rule-item"><span class="rule-kw">${escapeHtml(r.keyword)}</span><span class="rule-arrow">→</span><span class="rule-cat">${escapeHtml(r.category)}${r.subcategory ? " · " + escapeHtml(r.subcategory) : ""}</span><button class="rule-del" data-id="${r.id}" title="Remove">×</button></li>`
              )
              .join("")
        : `<li class="hint">No rules yet.</li>`;
    ul.querySelectorAll(".rule-del").forEach((b) =>
        b.addEventListener("click", async () => {
            await api(`/rules/${b.dataset.id}`, { method: "DELETE" });
            renderRules();
        })
    );
    document.getElementById("ruleCategory").innerHTML = categoryOptions(null, "expense");
}

function renderWalletExample() {
    const base = window.location.origin;
    document.getElementById("walletExample").textContent =
        `${base}/api/wallet/payment` +
        `?card=1900&merchant=Maxi&amount=1240\n\n` +
        `Unknown cards are auto-registered. Add &user=Ognjen\n` +
        `(or &user=2) to assign a new card to a user.`;
}

// ----------------------------------------------------------------- events
document.getElementById("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn) return;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === btn));
    const tab = btn.dataset.tab;
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    document.getElementById(`view-${tab}`).classList.remove("hidden");
    if (tab === "analytics") loadAnalytics();
    if (tab === "settings") renderSettings();
});

function shiftMonth(delta) {
    const base = analyticsMonth || new Date();
    analyticsMonth = new Date(base.getFullYear(), base.getMonth() + delta, 1);
    loadAnalytics();
}
document.getElementById("periodPrev").addEventListener("click", () => shiftMonth(-1));
document.getElementById("periodNext").addEventListener("click", () => shiftMonth(1));
document.getElementById("periodToday").addEventListener("click", () => {
    analyticsMonth = null;
    loadAnalytics();
});

document.getElementById("typeToggle").addEventListener("click", (e) => {
    const btn = e.target.closest(".toggle-btn");
    if (!btn) return;
    currentType = btn.dataset.type;
    document.querySelectorAll(".toggle-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderCategorySelect();
});

document.getElementById("filters").addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-btn");
    if (!btn) return;
    currentFilter = btn.dataset.filter;
    document.querySelectorAll(".filter-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderList();
});

document.getElementById("txForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (currentUserId === null) return alert("Add a user first");
    await addTransaction({
        user_id: currentUserId,
        type: currentType,
        amount: document.getElementById("amount").value,
        account_id: Number(document.getElementById("txAccount").value) || null,
        merchant: document.getElementById("merchant").value,
        category: document.getElementById("category").value,
        date: document.getElementById("date").value,
        note: document.getElementById("note").value,
    });
    e.target.reset();
    setToday();
});

document.getElementById("transferForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const from = Number(document.getElementById("transferFrom").value);
    const to = Number(document.getElementById("transferTo").value);
    const amount = document.getElementById("transferAmount").value;
    if (!from || !to) return alert("Pick both accounts");
    if (from === to) return alert("Choose two different accounts");
    try {
        await api("/transfer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ from_account_id: from, to_account_id: to, amount }),
        });
        e.target.reset();
        await refreshAll();
    } catch (err) { alert(err.message); }
});

document.getElementById("addUserForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
        await api("/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: document.getElementById("newUserName").value,
                initial_balance: document.getElementById("newUserBalance").value || 0,
            }),
        });
        e.target.reset();
        await refreshAll();
    } catch (err) { alert(err.message); }
});

document.getElementById("addCategoryForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
        await api("/categories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: document.getElementById("newCategory").value,
                kind: document.getElementById("newCategoryKind").value,
            }),
        });
        e.target.reset();
        await loadConfig();
        renderCategorySelect();
        renderCategoryChips();
        renderList();
    } catch (err) { alert(err.message); }
});

document.getElementById("addRuleForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
        await api("/rules", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                keyword: document.getElementById("ruleKeyword").value,
                category: document.getElementById("ruleCategory").value,
                subcategory: document.getElementById("ruleSub").value,
            }),
        });
        e.target.reset();
        renderRules();
    } catch (err) { alert(err.message); }
});

function setToday() {
    document.getElementById("date").value = new Date().toISOString().slice(0, 10);
}

async function init() {
    setToday();
    await loadConfig();
    renderCategorySelect();
    await loadUsers();
    await loadTransactions();
}

init();
