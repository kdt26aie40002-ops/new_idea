"use strict";

// ---- 共通ユーティリティ ------------------------------------------------------
const $ = (sel) => document.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Teacher-Agent": "1" } };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : `エラー (HTTP ${res.status})`);
  }
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 7000 : 3500);
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

const fmt = (iso) => (iso ? new Date(iso).toLocaleString("ja-JP") : "");
const STATUS = {
  pending: ["承認待ち", "warn"], approved: ["承認済み", "ok"], executed: ["送信済み", "ok"],
  rejected: ["却下", ""], blocked: ["禁止事項でブロック", "danger"], failed: ["失敗", "danger"],
};
const statusTag = (s) => h("span", { class: "tag " + (STATUS[s]?.[1] || "") }, STATUS[s]?.[0] || s);

// ---- 表示設定（ブラウザに保存する見た目の好みのみ） ---------------------------
const UI_KEY = "teacher-agent-ui";
function loadUi() {
  try { return JSON.parse(localStorage.getItem(UI_KEY)) || {}; } catch { return {}; }
}
function saveUi(ui) {
  try { localStorage.setItem(UI_KEY, JSON.stringify(ui)); } catch { /* 保存できなくても動作は続ける */ }
}
function applyUi(ui = loadUi()) {
  const root = document.documentElement;
  if (ui.theme && ui.theme !== "system") root.dataset.theme = ui.theme; else delete root.dataset.theme;
  if (ui.accent) root.style.setProperty("--accent", ui.accent);
  if (ui.fontSize) root.style.setProperty("--font-size", ui.fontSize + "px");
  $("#sidebar").classList.toggle("hidden", !!ui.sidebarHidden);
}

// ---- ページ ---------------------------------------------------------------
const pages = {};

pages.dashboard = {
  title: "ダッシュボード",
  async render(root) {
    const [st, approvals, audit] = await Promise.all([
      api("/api/status"), api("/api/approvals?status=pending"), api("/api/audit?limit=8"),
    ]);
    root.append(
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h3", {}, "AI（LLM）"),
          h("div", {}, h("span", { class: "tag " + (st.llm.ok ? "ok" : "danger") }, st.llm.ok ? "利用可能" : "利用不可"),
            " ", st.llm.provider === "demo" ? "デモ LLM" : `Ollama / ${st.llm.model}`),
          h("div", { class: "muted" }, st.llm.message)),
        h("div", { class: "card" }, h("h3", {}, "Teams"), h("div", {}, h("span", { class: "tag warn" }, "モック")),
          h("div", { class: "muted" }, st.teams.message)),
        h("div", { class: "card" }, h("h3", {}, "未回答の学生の質問"), h("div", { class: "stat" }, st.unanswered)),
        h("div", { class: "card" }, h("h3", {}, "承認待ち"), h("div", { class: "stat" }, st.pending_approvals)),
        h("div", { class: "card" }, h("h3", {}, "改善提案"), h("div", { class: "stat" }, st.pending_proposals)),
      ),
      h("div", { class: "card" }, h("h3", {}, "承認待ちの操作"),
        approvals.length ? approvals.slice(0, 5).map((a) =>
          h("div", { class: "msg" }, h("a", { href: "#approvals" }, `#${a.id} ${a.summary}`))) :
          h("div", { class: "muted" }, "ありません")),
      h("div", { class: "card" }, h("h3", {}, "最近の実行履歴"), auditTable(audit)),
    );
  },
};

pages.chat = {
  title: "AI チャット",
  async render(root) {
    const log = h("div", { class: "chat-log" });
    const input = h("textarea", { placeholder: "例：学生からの未回答の質問を確認して（Ctrl+Enter で送信）" });
    const send = h("button", {}, "送信");
    const suggestions = ["学生からの未回答の質問を確認して", "田中さんへの返信案を作って", "学科会議の議事録を作って",
      "2年Aクラス 連絡を要約して", "for文の小テストを作って", "レポートの提出期限は？"];

    const renderMsg = (m) => {
      const isMe = m.role === "user";
      const b = h("div", { class: "bubble" + (isMe ? " me" : "") }, h("div", { class: "pre" }, m.text));
      if (!isMe) {
        const meta = m.meta || {};
        const info = h("div", { class: "row muted" },
          h("span", { class: "tag" }, meta.agent || "AI"),
          meta.llm === "demo" ? h("span", { class: "tag warn" }, "デモ LLM") : null,
          (meta.approval_ids || []).length ? h("a", { href: "#approvals" }, `承認待ち #${meta.approval_ids.join(", #")}`) : null,
          h("span", { class: "spacer" }),
          rateButtons(m));
        b.append(info);
      }
      return b;
    };

    const rateButtons = (m) => {
      if (m.rating) return h("span", {}, m.rating === 1 ? "👍 評価済み" : "👎 評価済み");
      const wrap = h("span", { class: "row" });
      const rate = (r) => guard(async () => {
        const comment = r === -1 ? (prompt("どこを直すべきか一言ください（任意）") || "") : "";
        await api(`/api/chat/${m.id}/rating`, { method: "POST", body: { rating: r, comment } });
        m.rating = r;
        wrap.replaceWith(rateButtons(m));
        toast("評価を記録しました。改善提案画面で改善案を生成できます。");
      });
      wrap.append(h("button", { class: "ghost", onclick: () => rate(1), title: "良い" }, "👍"),
        h("button", { class: "ghost", onclick: () => rate(-1), title: "悪い" }, "👎"));
      return wrap;
    };

    const submit = () => guard(async () => {
      const text = input.value.trim();
      if (!text) return;
      send.disabled = true;
      log.append(renderMsg({ role: "user", text }));
      input.value = "";
      const thinking = h("div", { class: "bubble muted" }, "考えています…");
      log.append(thinking);
      try {
        const r = await api("/api/chat", { method: "POST", body: { text } });
        thinking.replaceWith(renderMsg({ id: r.id, role: "assistant", text: r.reply, meta: r.meta }));
        refreshBadges();
      } catch (e) {
        thinking.replaceWith(h("div", { class: "bubble" }, h("span", { class: "tag danger" }, "エラー"), " ", e.message));
      } finally {
        send.disabled = false;
        window.scrollTo(0, document.body.scrollHeight);
      }
    });

    send.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); });

    const history = await api("/api/chat");
    history.forEach((m) => log.append(renderMsg(m)));
    root.append(
      h("div", { class: "suggest" }, suggestions.map((s) =>
        h("button", { class: "ghost", onclick: () => { input.value = s; input.focus(); } }, s))),
      log,
      h("div", { class: "chat-input" }, input, h("div", {}, send,
        h("button", { class: "ghost", onclick: () => guard(async () => {
          if (!confirm("AI との会話履歴を消去しますか？（記憶は消えません）")) return;
          await api("/api/chat", { method: "DELETE" }); navigate();
        }) }, "履歴消去"))),
    );
    window.scrollTo(0, document.body.scrollHeight);
  },
};

pages.teams = {
  title: "モック Teams",
  current: null,
  async render(root) {
    const [convs, users] = await Promise.all([api("/api/teams/conversations"), api("/api/teams/users")]);
    const names = Object.fromEntries(users.map((u) => [u.id, u.name]));
    this.current = this.current || convs[0]?.id;
    const list = h("div", { class: "card" }, h("div", { class: "muted" }, "本物の Teams の代わりに、学生や教員になりきって投稿できます。"),
      convs.map((c) => h("div", {
        class: "conv" + (c.id === this.current ? " active" : ""),
        onclick: () => { this.current = c.id; navigate(); },
      }, c.title, " ", h("span", { class: "tag" }, { chat: "個人", channel: "チャネル", meeting: "会議" }[c.kind]))));
    const conv = convs.find((c) => c.id === this.current);
    const msgs = conv ? await api(`/api/teams/conversations/${encodeURIComponent(conv.id)}/messages`) : [];
    const sender = h("select", {}, (conv?.participants || []).map((p) => h("option", { value: p }, names[p] || p)));
    const text = h("textarea", { placeholder: "メッセージ" });
    const pane = h("div", { class: "card" }, h("h3", {}, conv ? conv.title : ""),
      msgs.map((m) => h("div", { class: "msg" },
        h("div", { class: "row" }, h("b", {}, m.sender_name), h("span", { class: "muted" }, fmt(m.created_at))),
        h("div", { class: "pre" }, m.text))),
      conv ? h("div", {}, h("div", { class: "row" }, "投稿者：", sender), text,
        h("button", { onclick: () => guard(async () => {
          if (!text.value.trim()) return;
          await api(`/api/teams/conversations/${encodeURIComponent(conv.id)}/messages`,
            { method: "POST", body: { sender_id: sender.value, text: text.value } });
          navigate(); refreshBadges();
        }) }, "投稿（デモ用）")) : null);
    root.append(h("div", { class: "teams" }, list, pane));
  },
};

pages.approvals = {
  title: "承認待ち",
  filter: "pending",
  async render(root) {
    const rows = await api("/api/approvals" + (this.filter ? `?status=${this.filter}` : ""));
    const tabs = h("div", { class: "row", style: null },
      [["pending", "承認待ち"], ["blocked", "ブロック"], ["", "すべて"]].map(([v, l]) =>
        h("button", { class: this.filter === v ? "" : "ghost", onclick: () => { this.filter = v; navigate(); } }, l)));
    root.append(h("div", { class: "card muted" },
      "AI が作った送信操作です。AI は承認なしにメッセージを送れません。本文を修正すると内容が変わるため、もう一度「承認して送信」を押す必要があります。"),
    tabs, h("div", { style: null }, " "));
    if (!rows.length) root.append(h("div", { class: "card muted" }, "該当する操作はありません。"));
    for (const a of rows) root.append(this.card(a));
  },
  card(a) {
    const editable = a.status === "pending";
    const ta = h("textarea", editable ? {} : { readonly: true });
    ta.value = a.payload.text || "";
    const violations = a.status === "blocked" && a.result ? JSON.parse(a.result) : [];
    const card = h("div", { class: "card" },
      h("div", { class: "row" }, h("b", {}, `#${a.id}`), statusTag(a.status), h("span", {}, a.summary),
        h("span", { class: "spacer" }), h("span", { class: "muted" }, fmt(a.created_at))),
      h("div", { class: "muted" }, "実行結果の予測：", a.prediction),
      h("div", { class: "muted" }, `送信先 ID：${a.payload.conversation_id} ／ 内容ハッシュ：${a.payload_hash.slice(0, 12)}… ／ 起点：${a.origin}`),
      violations.length ? h("div", {}, violations.map((v) => h("div", { class: "tag danger" }, v))) : null,
      ta);
    if (editable) {
      card.append(h("div", { class: "row" },
        h("button", { onclick: () => guard(async () => {
          if (ta.value !== a.payload.text) {
            await api(`/api/approvals/${a.id}/edit`, { method: "POST", body: { text: ta.value } });
            toast("修正を保存しました。内容が変わったので、確認してもう一度「承認して送信」を押してください。");
            return navigate();
          }
          if (!confirm(`この内容で送信します。よろしいですか？\n\n${a.summary}`)) return;
          await api(`/api/approvals/${a.id}/approve`, { method: "POST", body: { payload_hash: a.payload_hash } });
          toast("送信しました。"); navigate(); refreshBadges();
        }) }, "承認して送信"),
        h("button", { class: "ghost", onclick: () => guard(async () => {
          await api(`/api/approvals/${a.id}/edit`, { method: "POST", body: { text: ta.value } });
          toast("修正を保存しました。"); navigate();
        }) }, "修正を保存"),
        h("button", { class: "ghost", onclick: () => guard(async () => {
          await api(`/api/approvals/${a.id}/reject`, { method: "POST" }); navigate(); refreshBadges();
        }) }, "却下")));
    } else if (a.status === "blocked") {
      card.append(h("button", { class: "ghost", onclick: () => guard(async () => {
        await api(`/api/approvals/${a.id}/reject`, { method: "POST" }); navigate();
      }) }, "却下して片付ける"));
    }
    return card;
  },
};

pages.proposals = {
  title: "改善提案",
  async render(root) {
    const [props, fb] = await Promise.all([api("/api/proposals"), api("/api/feedback")]);
    const unprocessed = fb.filter((f) => !f.processed).length;
    root.append(h("div", { class: "card" },
      h("div", {}, "返信案の修正やチャットの評価から、AI が「記憶」の改善案を作ります。承認したものだけが記憶に反映されます。"),
      h("div", { class: "row" }, h("span", { class: "muted" }, `未処理の修正・評価：${unprocessed} 件`),
        h("button", { disabled: !unprocessed, onclick: () => guard(async () => {
          const r = await api("/api/proposals/generate", { method: "POST" });
          toast(`改善案を ${r.length} 件作成しました。`); navigate(); refreshBadges();
        }) }, "改善案を生成"))));
    if (!props.length) root.append(h("div", { class: "card muted" }, "改善案はまだありません。"));
    for (const p of props) {
      const ta = h("textarea", p.status === "pending" ? {} : { readonly: true });
      ta.value = p.content;
      const card = h("div", { class: "card" },
        h("div", { class: "row" }, h("b", {}, `#${p.id}`), h("span", { class: "tag" }, KIND_LABELS[p.memory_kind] || p.memory_kind),
          h("span", { class: "tag " + (p.status === "approved" ? "ok" : p.status === "pending" ? "warn" : "") },
            { pending: "未決定", approved: "承認・反映済み", rejected: "却下" }[p.status])),
        h("div", { class: "muted" }, "根拠：", p.rationale), ta);
      if (p.status === "pending") card.append(h("div", { class: "row" },
        h("button", { onclick: () => guard(async () => {
          await api(`/api/proposals/${p.id}/approve`, { method: "POST", body: { content: ta.value } });
          toast("記憶に反映しました。"); navigate(); refreshBadges();
        }) }, "承認して記憶に反映"),
        h("button", { class: "ghost", onclick: () => guard(async () => {
          await api(`/api/proposals/${p.id}/reject`, { method: "POST" }); navigate(); refreshBadges();
        }) }, "却下")));
      root.append(card);
    }
  },
};

let KIND_LABELS = {};

pages.memory = {
  title: "記憶・知識",
  kind: "", q: "",
  async render(root) {
    const kinds = await api("/api/memory-kinds");
    KIND_LABELS = Object.fromEntries(kinds.map((k) => [k.kind, k.label]));
    const params = new URLSearchParams();
    if (this.kind) params.set("kind", this.kind);
    if (this.q) params.set("q", this.q);
    const rows = await api("/api/memories?" + params);
    const kindSel = h("select", { onchange: (e) => { this.kind = e.target.value; navigate(); } },
      h("option", { value: "" }, "すべての種類"), kinds.map((k) => h("option", { value: k.kind, selected: k.kind === this.kind }, k.label)));
    const q = h("input", { placeholder: "意味の近い記憶を検索", value: this.q });
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") { this.q = q.value; navigate(); } });
    const newKind = h("select", {}, kinds.map((k) => h("option", { value: k.kind }, k.label)));
    const newText = h("textarea", { placeholder: "例：小テストの再受験は1回まで認める。" });
    root.append(
      h("div", { class: "card" }, h("h3", {}, "記憶を追加"), h("div", { class: "row" }, "種類：", newKind), newText,
        h("button", { onclick: () => guard(async () => {
          await api("/api/memories", { method: "POST", body: { kind: newKind.value, content: newText.value } });
          toast("追加しました。"); navigate();
        }) }, "追加")),
      h("div", { class: "row" }, kindSel, q, h("button", { class: "ghost", onclick: () => { this.q = q.value; navigate(); } }, "検索"),
        this.q ? h("button", { class: "ghost", onclick: () => { this.q = ""; navigate(); } }, "検索解除") : null),
      h("p", {}),
      ...rows.map((m) => this.card(m)));
  },
  card(m) {
    const ta = h("textarea", { class: "short" });
    ta.value = m.content;
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("span", { class: "tag" }, KIND_LABELS[m.kind] || m.kind),
        m.score !== undefined ? h("span", { class: "tag" }, `類似度 ${m.score}`) : null,
        h("span", { class: "muted" }, `出典：${m.source} ／ 登録 ${fmt(m.created_at)} ／ 更新 ${fmt(m.updated_at)}`)),
      ta,
      h("div", { class: "row" },
        h("button", { class: "ghost", onclick: () => guard(async () => {
          await api(`/api/memories/${m.id}`, { method: "PUT", body: { content: ta.value } }); toast("更新しました。");
        }) }, "更新"),
        h("button", { class: "danger", onclick: () => guard(async () => {
          if (!confirm("この記憶を削除しますか？")) return;
          await api(`/api/memories/${m.id}`, { method: "DELETE" }); navigate();
        }) }, "削除")));
  },
};

pages.prohibitions = {
  title: "禁止事項",
  async render(root) {
    const [rows, actions] = await Promise.all([api("/api/prohibitions"), api("/api/actions")]);
    const kind = h("select", {}, h("option", { value: "keyword" }, "禁止語（本文に含めない）"),
      h("option", { value: "recipient" }, "送信先（会話 ID・名前）"), h("option", { value: "action" }, "操作の種類"));
    const value = h("input", { placeholder: "値（例：成績 / ch-2a / teams.send_message）" });
    const note = h("input", { placeholder: "メモ（任意）" });
    root.append(
      h("div", { class: "card muted" },
        "禁止事項は AI のプロンプトではなく、サーバー側の実行制御で強制されます。AI がこの設定を変更する手段はありません。",
        h("br"), "AI が実行しうる操作：", actions.map((a) => h("span", { class: "tag" }, `${a.type}（${a.label}・承認必須）`))),
      h("div", { class: "card" }, h("h3", {}, "禁止事項を追加"), h("div", { class: "row" }, kind, value, note,
        h("button", { onclick: () => guard(async () => {
          await api("/api/prohibitions", { method: "POST", body: { kind: kind.value, value: value.value, note: note.value } });
          toast("追加しました。"); navigate();
        }) }, "追加"))),
      h("div", { class: "card" }, h("table", {},
        h("tr", {}, h("th", {}, "種類"), h("th", {}, "値"), h("th", {}, "メモ"), h("th", {}, "有効"), h("th", {}, "")),
        rows.map((p) => h("tr", {},
          h("td", {}, { keyword: "禁止語", recipient: "送信先", action: "操作" }[p.kind]),
          h("td", {}, p.value), h("td", {}, p.note),
          h("td", {}, h("input", { type: "checkbox", checked: !!p.enabled, onchange: (e) => guard(async () => {
            await api(`/api/prohibitions/${p.id}`, { method: "PUT", body: { enabled: e.target.checked } });
            toast(e.target.checked ? "有効にしました。" : "無効にしました。");
          }) })),
          h("td", {}, h("button", { class: "ghost", onclick: () => guard(async () => {
            if (!confirm(`禁止事項「${p.value}」を削除しますか？`)) return;
            await api(`/api/prohibitions/${p.id}`, { method: "DELETE" }); navigate();
          }) }, "削除")))))));
  },
};

function auditTable(rows) {
  return h("table", {}, h("tr", {}, h("th", {}, "日時"), h("th", {}, "主体"), h("th", {}, "イベント"), h("th", {}, "詳細")),
    rows.map((r) => h("tr", {}, h("td", { class: "muted" }, fmt(r.ts)),
      h("td", {}, { agent: "AI", teacher: "教員", system: "システム" }[r.actor]), h("td", {}, r.event),
      h("td", { class: "muted" }, Object.entries(r.detail).map(([k, v]) => `${k}=${v}`).join(" ")))));
}

pages.audit = {
  title: "実行履歴",
  async render(root) {
    root.append(h("div", { class: "card" }, h("div", { class: "muted" }, "メッセージ本文などの機密情報は記録していません。"),
      auditTable(await api("/api/audit?limit=300"))));
  },
};

pages.settings = {
  title: "設定",
  async render(root) {
    const s = await api("/api/settings");
    const st = await api("/api/status");
    const provider = h("select", {}, h("option", { value: "demo", selected: s.llm_provider === "demo" }, "デモ LLM（テンプレート応答）"),
      h("option", { value: "ollama", selected: s.llm_provider === "ollama" }, "Ollama（ローカル LLM）"));
    const url = h("input", { value: s.ollama_url, size: 30 });
    const model = h("input", { value: s.ollama_model });
    const models = st.llm.models || [];
    const ui = loadUi();
    const theme = h("select", {}, [["system", "OS に合わせる"], ["light", "ライト"], ["dark", "ダーク"]].map(([v, l]) =>
      h("option", { value: v, selected: (ui.theme || "system") === v }, l)));
    const accent = h("input", { type: "color", value: ui.accent || "#4f5bd5" });
    const font = h("input", { type: "range", min: 13, max: 20, value: ui.fontSize || 15 });
    const sidebar = h("input", { type: "checkbox", checked: !ui.sidebarHidden });
    const updateUi = () => { const n = { theme: theme.value, accent: accent.value, fontSize: +font.value, sidebarHidden: !sidebar.checked }; saveUi(n); applyUi(n); };
    [theme, accent, font, sidebar].forEach((el) => el.addEventListener("input", updateUi));

    root.append(
      h("div", { class: "card" }, h("h3", {}, "AI モデル"),
        h("div", { class: "row" }, "使用する LLM：", provider),
        h("div", { class: "row" }, "Ollama URL：", url, "モデル：", model),
        models.length ? h("div", { class: "muted" }, "Ollama にあるモデル：", models.join(", ")) : null,
        h("div", { class: "muted" }, "現在の状態：", st.llm.message),
        h("div", { class: "muted" }, "Ollama に接続できないときはエラーを表示します。デモ LLM や外部 AI へ自動で切り替えることはありません。"),
        h("button", { onclick: () => guard(async () => {
          await api("/api/settings", { method: "PUT", body: { llm_provider: provider.value, ollama_url: url.value, ollama_model: model.value } });
          toast("保存しました。"); navigate(); refreshBadges();
        }) }, "保存")),
      h("div", { class: "card" }, h("h3", {}, "表示（このブラウザに保存）"),
        h("div", { class: "row" }, "テーマ：", theme, "アクセントカラー：", accent),
        h("div", { class: "row" }, "文字サイズ：", font, h("label", {}, sidebar, " サイドバーを表示"))),
    );
  },
};

// ---- ルーティング ----------------------------------------------------------
async function navigate() {
  const name = location.hash.slice(1) || "dashboard";
  const page = pages[name] || pages.dashboard;
  document.querySelectorAll(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  $("#page-title").textContent = page.title;
  const root = h("div");
  try {
    if (!Object.keys(KIND_LABELS).length) {
      const kinds = await api("/api/memory-kinds");
      KIND_LABELS = Object.fromEntries(kinds.map((k) => [k.kind, k.label]));
    }
    await page.render(root);
  } catch (e) {
    root.append(h("div", { class: "card" }, h("span", { class: "tag danger" }, "エラー"), " ", e.message));
  }
  $("#page").replaceChildren(root);
}

async function refreshBadges() {
  try {
    const st = await api("/api/status");
    $("#badge-approvals").textContent = st.pending_approvals || "";
    $("#badge-proposals").textContent = st.pending_proposals || "";
    $("#llm-pill").textContent = `LLM: ${st.llm.provider === "demo" ? "デモ" : st.llm.model} ${st.llm.ok ? "●接続中" : "×未接続"}`;
  } catch { /* サーバー停止中など */ }
}

$("#toggle-sidebar").addEventListener("click", () => {
  const ui = loadUi(); ui.sidebarHidden = !ui.sidebarHidden; saveUi(ui); applyUi(ui);
});
window.addEventListener("hashchange", navigate);
applyUi();
navigate();
refreshBadges();
setInterval(refreshBadges, 15000);
