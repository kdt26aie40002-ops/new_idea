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

function icon(id) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#i-${id}`);
  svg.append(use);
  return svg;
}

const AVATAR_COLORS = ["c1", "c2", "c3", "c4"];
function avatar(name, cls = "", label = null) {
  const n = [...(name || "?")].reduce((a, c) => a + c.codePointAt(0), 0);
  return h("div", { class: `avatar ${cls || AVATAR_COLORS[n % AVATAR_COLORS.length]}` }, label ?? [...(name || "?")][0]);
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

// AI の出力（Markdown）を最低限の見出し・リスト・太字だけ整形する。
// 学生の文章が混ざりうるので innerHTML は使わず、テキストノードで組み立てる。
function md(text) {
  const inline = (s) => s.split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter(Boolean).map((part) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? h("strong", {}, part.slice(2, -2))
      : part.startsWith("`") && part.endsWith("`") && part.length > 2 ? h("code", {}, part.slice(1, -1)) : part);
  const root = h("div", { class: "md" });
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const head = line.match(/^(#{1,4})\s+(.*)$/);
    const item = line.match(/^\s*(?:[-*・]|\d+[.)])\s+(\[[ xX]\]\s+)?(.*)$/);
    if (item) {
      const tag = /^\s*\d/.test(line) ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== tag || /^\s{2,}/.test(raw) !== list._nested) {
        list = h(tag, { start: tag === "ol" ? line.match(/\d+/)[0] : null });
        list._nested = /^\s{2,}/.test(raw); root.append(list);
      }
      list.append(h("li", { class: item[1] ? "todo" + (/x/i.test(item[1]) ? " done" : "") : null }, inline(item[2])));
      continue;
    }
    if (line.trim() && list && /^\s{2,}/.test(raw)) { list.lastChild?.append(h("br"), ...inline(line.trim())); continue; }
    list = null;
    if (head) root.append(h("h4", {}, inline(head[2])));
    else if (line.trim()) root.append(h("p", {}, inline(line)));
  }
  return root;
}

// ---- 動きと手応え ----------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const coarsePointer = matchMedia("(pointer: coarse)");
const EASE_OUT = "cubic-bezier(.22, 1, .36, 1)";

// 触覚：指で操作する端末だけ、ごく短く震わせる（対応していない端末では何もしない）
function haptic(pattern = 6) {
  if (!coarsePointer.matches || !navigator.vibrate) return;
  try { navigator.vibrate(pattern); } catch { /* 無視 */ }
}
document.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "touch" && e.target.closest("button:not(:disabled), a, .conv")) haptic(6);
}, { passive: true });
document.addEventListener("change", (e) => { if (e.target.matches?.('input[type="checkbox"]')) haptic(10); });

// 映り込み：カーソルの位置をカードに渡し、ガラスの光沢を追従させる（1 フレームに 1 回だけ計算）
let specEvent = null;
document.addEventListener("pointermove", (e) => {
  if (e.pointerType !== "mouse") return;
  const first = !specEvent;
  specEvent = e;
  if (!first) return;
  requestAnimationFrame(() => {
    const ev = specEvent;
    specEvent = null;
    for (let el = ev.target.closest?.(".card, .ai-tile"); el; el = el.parentElement?.closest(".card, .ai-tile")) {
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${ev.clientX - r.left}px`);
      el.style.setProperty("--my", `${ev.clientY - r.top}px`);
    }
  });
}, { passive: true });

// 数字は 0 から数え上がる
function countUp(el) {
  const to = Number(el.textContent);
  if (reducedMotion.matches || !Number.isInteger(to) || to <= 0) return;
  const t0 = performance.now(), dur = 650 + Math.min(to, 20) * 15;
  const step = (t) => {
    const p = Math.min((t - t0) / dur, 1);
    el.textContent = Math.round(to * (1 - Math.pow(1 - p, 4)));
    if (p < 1) requestAnimationFrame(step);
  };
  el.textContent = "0";
  requestAnimationFrame(step);
}

// 新しい画面のカードに、少しずつずらした登場アニメーションを付ける
function animateIn(root) {
  if (reducedMotion.matches) return;
  const items = [];
  for (const el of root.children) {
    if (el.matches(".grid, .two-col, .teams")) items.push(...el.children); else items.push(el);
  }
  items.forEach((el, i) => { el.classList.add("enter"); el.style.setProperty("--i", Math.min(i, 10)); });
  root.querySelectorAll(".ai-tile").forEach((el, i) => { el.classList.add("enter"); el.style.setProperty("--i", 5 + Math.min(i, 8)); });
  root.querySelectorAll(".stat").forEach(countUp);
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  t.style.animation = "none"; void t.offsetWidth; t.style.animation = "";  // 連続表示でも毎回現れ直す
  haptic(isError ? [14, 60, 14] : 8);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, isError ? 7000 : 3500);
}
function hideToast() {
  const t = $("#toast");
  if (t.hidden) return;
  clearTimeout(toastTimer);
  t.classList.add("out");
  t.addEventListener("animationend", () => { if (t.classList.contains("out")) t.hidden = true; }, { once: true });
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

const fmt = (iso) => (iso ? new Date(iso).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const STATUS = {
  pending: ["承認待ち", "warn"], approved: ["承認済み", "ok"], executed: ["送信済み", "ok"],
  rejected: ["却下", ""], blocked: ["禁止事項でブロック", "danger"], failed: ["失敗", "danger"],
};
const statusTag = (s) => h("span", { class: "tag " + (STATUS[s]?.[1] || "") }, STATUS[s]?.[0] || s);
const note = (...children) => h("div", { class: "card note" }, icon("shield"), h("div", {}, ...children));
const empty = (emoji, text) => h("div", { class: "card empty" }, h("div", { class: "big" }, emoji), text);
const field = (label, ...control) => h("label", { class: "field" }, h("span", {}, label), ...control);

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
  if (ui.accent) root.style.setProperty("--accent", ui.accent); else root.style.removeProperty("--accent");
  if (ui.fontSize) root.style.setProperty("--font-size", ui.fontSize + "px");
  $("#sidebar").classList.toggle("hidden", !!ui.sidebarHidden);
}

// ---- ページ ---------------------------------------------------------------
const pages = {};
let KIND_LABELS = {};

function statCard(iconId, tone, label, value, foot, href) {
  return h("a", { class: "card stat-card", href, style: null },
    h("div", { class: "stat-head" }, h("div", { class: `stat-icon ${tone}` }, icon(iconId)), label),
    h("div", { class: "stat" }, value),
    h("div", { class: "stat-foot" }, foot));
}

pages.dashboard = {
  title: "ダッシュボード",
  sub: "今日の状況をひと目で",
  async render(root) {
    const [st, approvals, audit, convs] = await Promise.all([
      api("/api/status"), api("/api/approvals?status=pending"), api("/api/audit?limit=6"),
      api("/api/teams/conversations"),
    ]);
    const hour = new Date().getHours();
    const greet = hour < 11 ? "おはようございます" : hour < 18 ? "こんにちは" : "おつかれさまです";
    root.append(
      h("div", { class: "hero" },
        h("div", { class: "eyebrow" }, new Date().toLocaleDateString("ja-JP", { month: "long", day: "numeric", weekday: "long" })),
        h("h2", {}, `${greet}、`, h("span", { class: "ai-word" }, "佐藤先生")),
        h("p", {}, st.unanswered ? `学生からの未回答の質問が ${st.unanswered} 件あります。返信案を用意しましょうか？`
          : "未回答の質問はありません。落ち着いた一日になりそうです。"),
        h("button", { onclick: () => { sessionStorage.setItem("prefill", "学生からの未回答の質問を確認して"); location.hash = "#chat"; } },
          icon("chat"), "AI に頼む")),
      h("div", { class: "grid" },
        statCard("teams", "pink", "未回答の質問", st.unanswered, "学生の個人チャット", "#teams"),
        statCard("check", "amber", "承認待ち", st.pending_approvals, "送信前の確認が必要", "#approvals"),
        statCard("spark", "", "改善提案", st.pending_proposals, "承認すると記憶に反映", "#proposals"),
        statCard("brain", "green", "AI", st.llm.ok ? "接続中" : "未接続",
          st.llm.provider === "demo" ? "デモ LLM" : `Ollama · ${st.llm.model}`, "#settings")),
      this.aiPanel(st, convs),
      h("div", { class: "two-col" },
        h("div", { class: "card" }, h("h3", {}, "承認待ちの操作"),
          approvals.length ? approvals.slice(0, 5).map((a) =>
            h("a", { class: "list-item", href: "#approvals" }, avatar(a.summary.match(/「(.+?)」/)?.[1] || "?"),
              h("div", { class: "spacer" }, h("div", { class: "li-title" }, a.summary), h("div", { class: "muted" }, fmt(a.created_at))),
              statusTag(a.status))) :
            h("div", { class: "empty" }, h("div", { class: "big" }, "✨"), "承認待ちはありません")),
        h("div", { class: "card" }, h("h3", {}, "最近のアクティビティ"), auditTable(audit, true))),
    );
  },

  // AI にワンクリックで頼める操作。結果は AI チャットの履歴にも残る（評価・続きの会話ができる）
  aiPanel(st, convs) {
    const result = h("div", { class: "ai-result", hidden: true });
    const buttons = [];
    let busy = false;
    const run = (title, body, path = "/api/dashboard/ai") => guard(async () => {
      if (busy) return;
      busy = true;
      buttons.forEach((b) => (b.disabled = true));
      result.hidden = false;
      result.classList.add("loading");
      result.replaceChildren(h("div", { class: "row" }, h("b", {}, title), h("span", { class: "thinking" }, h("i"), h("i"), h("i"))),
        h("div", { class: "muted" }, st.llm.provider === "ollama" ? "ローカル LLM で考えています（数秒〜1分ほど）…" : "作成中…"));
      try {
        const r = await api(path, { method: "POST", body });
        const copy = h("button", { class: "ghost", onclick: () => guard(async () => {
          await navigator.clipboard.writeText(r.reply); toast("コピーしました。");
        }) }, "コピー");
        result.replaceChildren(
          h("div", { class: "row" }, h("b", {}, title), h("span", { class: "tag accent" }, r.meta.agent),
            r.meta.llm === "demo" ? h("span", { class: "tag warn" }, "デモ LLM") : null, h("span", { class: "spacer" }),
            h("button", { class: "ghost", onclick: () => { result.hidden = true; } }, "閉じる")),
          h("div", { class: "ai-output" }, md(r.reply)),
          h("div", { class: "row" }, copy,
            r.meta.approval_ids.length ? h("button", { onclick: () => { location.hash = "#approvals"; } },
              icon("check"), `承認待ちを確認（${r.meta.approval_ids.length} 件）`) : null,
            h("button", { class: "ghost", onclick: () => { location.hash = "#chat"; } }, icon("chat"), "チャットで続ける"),
            h("span", { class: "spacer" }), h("span", { class: "muted" }, "AI チャットの履歴にも保存しました")));
        refreshBadges();
      } catch (e) {
        result.replaceChildren(h("span", { class: "tag danger" }, "エラー"), " ", e.message);
      } finally {
        buttons.forEach((b) => (b.disabled = false));
        busy = false;
        result.classList.remove("loading");
      }
    });
    const btn = (label, onclick, cls = "") => { const b = h("button", { class: cls, onclick }, label); buttons.push(b); return b; };
    const tile = ([iconId, tone], title, desc, ...controls) => h("div", { class: "ai-tile" },
      h("div", { class: "ai-tile-head" }, h("span", { class: `ai-emoji ${tone}` }, icon(iconId)), h("b", {}, title)),
      h("div", { class: "muted" }, desc), h("div", { class: "row ai-controls" }, ...controls));
    const convSelect = (kinds) => h("select", {}, convs.filter((c) => kinds.includes(c.kind)).map((c) => h("option", { value: c.id }, c.title)));
    const sumConv = convSelect(["chat", "channel", "meeting"]);
    const meeting = convSelect(["meeting"]);
    const topic = h("input", { placeholder: "例：for文", class: "spacer" });
    const kind = h("select", {}, ["授業資料", "課題", "小テスト"].map((k) => h("option", { value: k }, k)));
    const ask = h("input", { placeholder: "例：レポートの提出期限は？", class: "spacer" });
    const askRun = () => ask.value.trim() && run("AI に質問", { text: ask.value.trim() }, "/api/chat");
    const materialRun = () => {
      if (!topic.value.trim()) return toast("テーマを入力してください。", true);
      run(`${kind.value}：${topic.value.trim()}`, { action: "material", topic: topic.value.trim(), kind: kind.value });
    };
    ask.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) askRun(); });
    topic.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) materialRun(); });

    return h("div", { class: "card ai-card" },
      h("div", { class: "row" }, h("h3", {}, h("span", { class: "ai-word" }, "AI"), " でできること"), h("span", { class: "spacer" }),
        h("span", { class: "muted" }, "送信は必ず承認待ちを通ります")),
      h("div", { class: "ai-grid" },
        tile(["calendar", "red"], "今日のブリーフィング", "未回答の質問・承認待ち・締め切りから、今日やることを優先順に",
          btn("まとめる", () => run("今日のブリーフィング", { action: "briefing" }))),
        tile(["mail", "blue"], "未回答の質問に返信案", `${st.unanswered} 件の質問に下書きを作り、承認待ちに入れます`,
          btn("返信案を作る", () => run("未回答の質問への返信案", { action: "unanswered" }))),
        tile(["chart", "purple"], "質問の傾向分析", "学生のつまずき・登録すべき知識・クラスへの連絡案",
          btn("分析する", () => run("学生の質問の傾向分析", { action: "insights" }))),
        tile(["list", "orange"], "会話を要約", "Teams のチャット・チャネル・会議を箇条書きに",
          sumConv, btn("要約", () => run(`要約：${sumConv.selectedOptions[0]?.text || ""}`, { action: "summarize", conversation_id: sumConv.value }))),
        tile(["doc", "green"], "議事録を作成", "参加者・決定事項・ToDo を整理",
          meeting, btn("作成", () => run(`議事録：${meeting.selectedOptions[0]?.text || ""}`, { action: "minutes", conversation_id: meeting.value }))),
        tile(["book", "pink"], "教材をつくる", "授業資料・課題・小テストの下書き", kind, topic, btn("作成", materialRun)),
        h("div", { class: "ai-tile wide" },
          h("div", { class: "ai-tile-head" }, h("span", { class: "ai-emoji teal" }, icon("chat")), h("b", {}, "ひとこと質問"),
            h("span", { class: "muted" }, "記憶をもとに答えます")),
          h("div", { class: "row ai-controls" }, ask, btn("聞く", askRun)))),
      result);
  },
};

pages.chat = {
  title: "AI チャット",
  sub: "自然な言葉で頼んでください",
  async render(root) {
    const log = h("div", { class: "chat-log" });
    const input = h("textarea", { placeholder: "メッセージを入力…（Ctrl+Enter で送信）", rows: 1 });
    const send = h("button", { class: "send" }, icon("send"), "送信");
    const suggestions = ["学生からの未回答の質問を確認して", "田中さんへの返信案を作って", "学科会議の議事録を作って",
      "2年Aクラス 連絡を要約して", "for文の小テストを作って", "レポートの提出期限は？"];

    const rateButtons = (m) => {
      if (m.rating) return h("span", { class: "tag " + (m.rating === 1 ? "ok" : "danger") }, m.rating === 1 ? "👍 評価済み" : "👎 評価済み");
      const wrap = h("span", { class: "row" });
      const rate = (r) => guard(async () => {
        const comment = r === -1 ? (prompt("どこを直すべきか一言ください（任意）") || "") : "";
        await api(`/api/chat/${m.id}/rating`, { method: "POST", body: { rating: r, comment } });
        m.rating = r;
        wrap.replaceWith(rateButtons(m));
        toast("評価を記録しました。改善提案画面で改善案を生成できます。");
      });
      wrap.append(h("button", { class: "rate", onclick: () => rate(1), title: "良い" }, "👍"),
        h("button", { class: "rate", onclick: () => rate(-1), title: "悪い" }, "👎"));
      return wrap;
    };

    const renderMsg = (m) => {
      const isMe = m.role === "user";
      const bubble = h("div", { class: "bubble" }, isMe ? h("div", { class: "pre" }, m.text) : md(m.text));
      if (!isMe) {
        const meta = m.meta || {};
        bubble.append(h("div", { class: "bubble-meta" },
          h("span", { class: "tag accent" }, meta.agent || "AI"),
          meta.llm === "demo" ? h("span", { class: "tag warn" }, "デモ LLM") : null,
          (meta.approval_ids || []).length ? h("a", { href: "#approvals" }, `→ 承認待ち #${meta.approval_ids.join(", #")}`) : null,
          h("span", { class: "spacer" }),
          m.id ? rateButtons(m) : null));
      }
      return h("div", { class: "msg-row" + (isMe ? " me" : "") }, avatar(isMe ? "佐" : "AI", isMe ? "user" : "ai", isMe ? "佐" : "AI"), bubble);
    };

    const autosize = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 220) + "px"; };
    const submit = () => guard(async () => {
      const text = input.value.trim();
      if (!text) return;
      send.disabled = true;
      log.append(renderMsg({ role: "user", text }));
      input.value = ""; autosize();
      const thinking = h("div", { class: "msg-row" }, avatar("AI", "ai", "AI"),
        h("div", { class: "bubble" }, h("span", { class: "thinking" }, h("i"), h("i"), h("i"))));
      log.append(thinking);
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
      try {
        const r = await api("/api/chat", { method: "POST", body: { text } });
        thinking.replaceWith(renderMsg({ id: r.id, role: "assistant", text: r.reply, meta: r.meta }));
        refreshBadges();
      } catch (e) {
        thinking.replaceWith(h("div", { class: "msg-row" }, avatar("!", "c1"),
          h("div", { class: "bubble" }, h("span", { class: "tag danger" }, "エラー"), " ", e.message)));
      } finally {
        send.disabled = false;
        window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
      }
    });

    send.addEventListener("click", submit);
    input.addEventListener("input", autosize);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); });

    const history = await api("/api/chat");
    history.forEach((m) => log.append(renderMsg(m)));
    if (!history.length) {
      log.append(h("div", { class: "empty" }, h("div", { class: "big" }, "👋"),
        h("div", {}, "こんにちは。佐藤先生の「第二の自分」です。"),
        h("div", { class: "muted" }, "下の例から選ぶか、自由に話しかけてください。送信などの操作は必ず先生の承認を待ちます。")));
    }
    const prefill = sessionStorage.getItem("prefill");
    if (prefill) { input.value = prefill; sessionStorage.removeItem("prefill"); }
    root.append(h("div", { class: "chat-wrap" },
      h("div", { class: "suggest" }, suggestions.map((s) =>
        h("button", { class: "chip", onclick: () => { input.value = s; autosize(); input.focus(); } }, s)),
        history.length ? h("button", { class: "chip", onclick: () => guard(async () => {
          if (!confirm("AI との会話履歴を消去しますか？（記憶は消えません）")) return;
          await api("/api/chat", { method: "DELETE" }); navigate();
        }) }, "🗑 履歴を消去") : null),
      log,
      h("div", { class: "composer" }, input, send)));
    this.mounted = () => window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" });
  },
};

pages.teams = {
  title: "モック Teams",
  sub: "本物の Teams の代わりのデモ環境です。学生や先生になりきって投稿できます",
  current: null,
  async render(root) {
    const [convs, users] = await Promise.all([api("/api/teams/conversations"), api("/api/teams/users")]);
    const names = Object.fromEntries(users.map((u) => [u.id, u.name]));
    this.current = this.current || convs[0]?.id;
    const KIND = { chat: "個人チャット", channel: "チャネル", meeting: "会議" };
    const list = h("div", { class: "card" },
      convs.map((c) => h("div", {
        class: "conv" + (c.id === this.current ? " active" : ""),
        onclick: () => { this.current = c.id; navigate(); },
      }, avatar(c.title), h("div", {}, h("div", { class: "name" }, c.title), h("div", { class: "sub" }, KIND[c.kind])))));
    const conv = convs.find((c) => c.id === this.current);
    const msgs = conv ? await api(`/api/teams/conversations/${encodeURIComponent(conv.id)}/messages`) : [];
    const sender = h("select", {}, (conv?.participants || []).map((p) => h("option", { value: p }, names[p] || p)));
    const text = h("textarea", { class: "short", placeholder: "メッセージを入力" });
    const pane = h("div", { class: "card" },
      h("div", { class: "row" }, h("h3", {}, conv ? conv.title : ""), conv ? h("span", { class: "tag" }, KIND[conv.kind]) : null),
      msgs.map((m) => h("div", { class: "tmsg" }, avatar(m.sender_name),
        h("div", {}, h("div", { class: "who" }, h("b", {}, m.sender_name), h("span", { class: "muted" }, fmt(m.created_at))),
          h("div", { class: "pre" }, m.text)))),
      conv ? h("div", { class: "composer" }, h("div", { class: "spacer" }, h("div", { class: "row muted" }, "投稿者", sender), text),
        h("button", { class: "send", onclick: () => guard(async () => {
          if (!text.value.trim()) return;
          await api(`/api/teams/conversations/${encodeURIComponent(conv.id)}/messages`,
            { method: "POST", body: { sender_id: sender.value, text: text.value } });
          navigate(); refreshBadges();
        }) }, icon("send"), "投稿")) : null);
    root.append(h("div", { class: "teams" }, list, pane));
  },
};

pages.approvals = {
  title: "承認待ち",
  sub: "AI は先生の承認なしに送信できません",
  filter: "pending",
  async render(root) {
    const rows = await api("/api/approvals" + (this.filter ? `?status=${this.filter}` : ""));
    root.append(
      note("本文を修正すると内容が変わるため、もう一度「承認して送信」を押すまで送信されません。禁止事項に当たる操作は自動でブロックされます。"),
      h("div", { class: "segment" }, [["pending", "承認待ち"], ["blocked", "ブロック"], ["", "すべて"]].map(([v, l]) =>
        h("button", { class: this.filter === v ? "on" : "", onclick: () => { this.filter = v; navigate(); } }, l))));
    if (!rows.length) root.append(empty("✅", "該当する操作はありません"));
    for (const a of rows) root.append(this.card(a));
  },
  card(a) {
    const editable = a.status === "pending";
    const ta = h("textarea", editable ? {} : { readonly: true });
    ta.value = a.payload.text || "";
    const violations = a.status === "blocked" && a.result ? JSON.parse(a.result) : [];
    const to = a.summary.match(/「(.+?)」/)?.[1] || "?";
    const card = h("div", { class: `card approval ${a.status}` },
      h("div", { class: "row" }, avatar(to), h("div", {}, h("div", { class: "li-title" }, a.summary),
        h("div", { class: "muted" }, `#${a.id} · ${fmt(a.created_at)}`)), h("span", { class: "spacer" }), statusTag(a.status)),
      h("dl", { class: "kv" },
        h("dt", {}, "実行後の予測"), h("dd", {}, a.prediction),
        h("dt", {}, "送信先 ID"), h("dd", { class: "mono" }, a.payload.conversation_id),
        h("dt", {}, "内容ハッシュ"), h("dd", { class: "mono" }, a.payload_hash.slice(0, 16) + "…"),
        h("dt", {}, "起点"), h("dd", { class: "mono" }, a.origin)),
      violations.length ? h("div", { class: "row" }, violations.map((v) => h("span", { class: "tag danger" }, v))) : null,
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
        }) }, icon("send"), "承認して送信"),
        h("button", { class: "ghost", onclick: () => guard(async () => {
          await api(`/api/approvals/${a.id}/edit`, { method: "POST", body: { text: ta.value } });
          toast("修正を保存しました。"); navigate();
        }) }, "修正を保存"),
        h("span", { class: "spacer" }),
        h("button", { class: "danger", onclick: () => guard(async () => {
          await api(`/api/approvals/${a.id}/reject`, { method: "POST" }); navigate(); refreshBadges();
        }) }, "却下")));
    } else if (a.status === "blocked") {
      card.append(h("div", { class: "row" }, h("button", { class: "ghost", onclick: () => guard(async () => {
        await api(`/api/approvals/${a.id}/reject`, { method: "POST" }); navigate();
      }) }, "却下して片付ける")));
    }
    return card;
  },
};

pages.proposals = {
  title: "改善提案",
  sub: "先生の修正や評価から、AI が自分を改善する案を出します",
  async render(root) {
    const [props, fb] = await Promise.all([api("/api/proposals"), api("/api/feedback")]);
    const unprocessed = fb.filter((f) => !f.processed).length;
    root.append(h("div", { class: "hero" },
      h("h2", {}, `未処理の修正・評価：${unprocessed} 件`),
      h("p", {}, "承認した改善案だけが記憶に反映されます。禁止事項や権限は改善の対象外です。"),
      h("button", { disabled: !unprocessed, onclick: () => guard(async () => {
        const r = await api("/api/proposals/generate", { method: "POST" });
        toast(`改善案を ${r.length} 件作成しました。`); navigate(); refreshBadges();
      }) }, icon("spark"), "改善案を生成")));
    if (!props.length) root.append(empty("🌱", "改善案はまだありません。返信案を修正したり、チャットを評価すると作れます。"));
    for (const p of props) {
      const ta = h("textarea", { class: "short", ...(p.status === "pending" ? {} : { readonly: true }) });
      ta.value = p.content;
      const card = h("div", { class: "card" },
        h("div", { class: "row" }, h("span", { class: "tag accent" }, KIND_LABELS[p.memory_kind] || p.memory_kind),
          h("span", { class: "tag " + (p.status === "approved" ? "ok" : p.status === "pending" ? "warn" : "") },
            { pending: "未決定", approved: "反映済み", rejected: "却下" }[p.status]),
          h("span", { class: "spacer" }), h("span", { class: "muted" }, `#${p.id} · ${fmt(p.created_at)}`)),
        h("p", { class: "muted" }, "根拠：", p.rationale), ta);
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

pages.memory = {
  title: "記憶・知識",
  sub: "AI が参考にする先生の知識・判断・話し方",
  kind: "", q: "",
  async render(root) {
    const kinds = await api("/api/memory-kinds");
    const params = new URLSearchParams();
    if (this.kind) params.set("kind", this.kind);
    if (this.q) params.set("q", this.q);
    const rows = await api("/api/memories?" + params);
    const q = h("input", { placeholder: "🔍 意味の近い記憶を検索", value: this.q, class: "spacer" });
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") { this.q = q.value; navigate(); } });
    const newKind = h("select", {}, kinds.map((k) => h("option", { value: k.kind }, k.label)));
    const newText = h("textarea", { class: "short", placeholder: "例：小テストの再受験は1回まで認める。" });
    root.append(
      h("div", { class: "card" }, h("h3", {}, "記憶を追加"),
        h("div", { class: "row" }, newKind, h("span", { class: "spacer" })), h("p"), newText, h("p"),
        h("button", { onclick: () => guard(async () => {
          await api("/api/memories", { method: "POST", body: { kind: newKind.value, content: newText.value } });
          toast("追加しました。"); navigate();
        }) }, "＋ 追加")),
      h("div", { class: "segment" },
        h("button", { class: this.kind ? "" : "on", onclick: () => { this.kind = ""; navigate(); } }, "すべて"),
        kinds.map((k) => h("button", { class: this.kind === k.kind ? "on" : "", onclick: () => { this.kind = k.kind; navigate(); } }, k.label))),
      h("div", { class: "row" }, q, h("button", { class: "ghost", onclick: () => { this.q = q.value; navigate(); } }, "検索"),
        this.q ? h("button", { class: "ghost", onclick: () => { this.q = ""; navigate(); } }, "解除") : null),
      h("p"),
      ...(rows.length ? rows.map((m) => this.card(m)) : [empty("📭", "記憶がありません")]));
  },
  card(m) {
    const ta = h("textarea", { class: "short" });
    ta.value = m.content;
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("span", { class: "tag accent" }, KIND_LABELS[m.kind] || m.kind),
        m.score !== undefined ? h("span", { class: "tag ok" }, `類似度 ${m.score}`) : null,
        h("span", { class: "spacer" }),
        h("span", { class: "muted" }, `${m.source} · 登録 ${fmt(m.created_at)}${m.updated_at !== m.created_at ? ` · 更新 ${fmt(m.updated_at)}` : ""}`)),
      h("p"), ta, h("p"),
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
  sub: "AI が絶対にしてはいけないこと",
  async render(root) {
    const [rows, actions] = await Promise.all([api("/api/prohibitions"), api("/api/actions")]);
    const kind = h("select", {}, h("option", { value: "keyword" }, "禁止語（本文に含めない）"),
      h("option", { value: "recipient" }, "送信先（会話 ID・名前）"), h("option", { value: "action" }, "操作の種類"));
    const value = h("input", { placeholder: "例：成績 / ch-2a / teams.send_message" });
    const memo = h("input", { placeholder: "メモ（任意）" });
    root.append(
      note("禁止事項は AI へのお願いではなく、サーバー側の仕組みで強制されます。AI がこの設定を変更する手段はありません。",
        h("div", { class: "row" }, "AI が実行しうる操作：", actions.map((a) => h("span", { class: "tag accent" }, `${a.label}（承認必須）`)))),
      h("div", { class: "card" }, h("h3", {}, "禁止事項を追加"),
        h("div", { class: "row" }, kind, value, memo,
          h("button", { onclick: () => guard(async () => {
            await api("/api/prohibitions", { method: "POST", body: { kind: kind.value, value: value.value, note: memo.value } });
            toast("追加しました。"); navigate();
          }) }, "＋ 追加"))),
      h("div", { class: "card" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "種類"), h("th", {}, "値"), h("th", {}, "メモ"), h("th", {}, "有効"), h("th", {}, ""))),
        h("tbody", {}, rows.map((p) => h("tr", {},
          h("td", {}, h("span", { class: "tag danger" }, { keyword: "禁止語", recipient: "送信先", action: "操作" }[p.kind])),
          h("td", { class: "li-title" }, p.value), h("td", { class: "muted" }, p.note),
          h("td", {}, h("input", { type: "checkbox", checked: !!p.enabled, onchange: (e) => guard(async () => {
            await api(`/api/prohibitions/${p.id}`, { method: "PUT", body: { enabled: e.target.checked } });
            toast(e.target.checked ? "有効にしました。" : "無効にしました。");
          }) })),
          h("td", {}, h("button", { class: "danger", onclick: () => guard(async () => {
            if (!confirm(`禁止事項「${p.value}」を削除しますか？`)) return;
            await api(`/api/prohibitions/${p.id}`, { method: "DELETE" }); navigate();
          }) }, "削除"))))))));
  },
};

const ACTOR = { agent: ["AI", "accent"], teacher: ["先生", "ok"], system: ["システム", ""] };
function auditTable(rows, compact = false) {
  return h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "日時"), h("th", {}, "主体"), h("th", {}, "イベント"), compact ? null : h("th", {}, "詳細"))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "muted" }, fmt(r.ts)),
      h("td", {}, h("span", { class: "tag " + ACTOR[r.actor][1] }, ACTOR[r.actor][0])), h("td", { class: "mono" }, r.event),
      compact ? null : h("td", { class: "muted mono" }, Object.entries(r.detail).map(([k, v]) => `${k}=${v}`).join("  "))))));
}

pages.audit = {
  title: "実行履歴",
  sub: "メッセージ本文などの機密情報は記録していません",
  async render(root) {
    root.append(h("div", { class: "card" }, auditTable(await api("/api/audit?limit=300"))));
  },
};

pages.settings = {
  title: "設定",
  sub: "AI モデルと表示のカスタマイズ",
  async render(root) {
    const [s, st] = await Promise.all([api("/api/settings"), api("/api/status")]);
    const provider = h("select", {}, h("option", { value: "demo", selected: s.llm_provider === "demo" }, "デモ LLM（テンプレート応答）"),
      h("option", { value: "ollama", selected: s.llm_provider === "ollama" }, "Ollama（ローカル LLM）"));
    const url = h("input", { value: s.ollama_url });
    const model = h("input", { value: s.ollama_model, list: "models" });
    const models = st.llm.models || [];
    const ui = loadUi();
    const theme = h("div", { class: "segment" });
    let themeVal = ui.theme || "system";
    const setTheme = (v) => { themeVal = v; [...theme.children].forEach((b) => b.classList.toggle("on", b.dataset.v === v)); updateUi(); };
    [["system", "🖥 自動"], ["light", "☀️ ライト"], ["dark", "🌙 ダーク"]].forEach(([v, l]) =>
      theme.append(h("button", { "data-v": v, class: themeVal === v ? "on" : "", onclick: () => setTheme(v) }, l)));
    const accent = h("input", { type: "color", value: ui.accent || "#0071e3" });
    const current = (ui.accent || "#0071e3").toLowerCase();
    const swatches = h("div", { class: "row swatches" }, ["#0071e3", "#4f5bd5", "#7a4fd6", "#0a7d8c", "#3a3a3c"].map((c) => {
      const b = h("button", { class: "swatch", title: c, "aria-label": `アクセントカラー ${c}`, "aria-pressed": String(c === current),
        onclick: () => {
          accent.value = c; updateUi();
          b.parentElement.querySelectorAll(".swatch").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        } });
      b.style.setProperty("--c", c);
      return b;
    }), accent);
    const font = h("input", { type: "range", min: 13, max: 20, value: ui.fontSize || 15 });
    const sidebar = h("input", { type: "checkbox", checked: !ui.sidebarHidden });
    function updateUi() {
      const n = { theme: themeVal, accent: accent.value, fontSize: +font.value, sidebarHidden: !sidebar.checked };
      saveUi(n); applyUi(n);
    }
    [accent, font, sidebar].forEach((el) => el.addEventListener("input", updateUi));

    root.append(h("div", { class: "two-col" },
      h("div", { class: "card" }, h("h3", {}, "AI モデル"),
        h("div", { class: "row" }, h("span", { class: "dot " + (st.llm.ok ? "ok" : "bad") }), h("span", {}, st.llm.message)),
        h("p"),
        field("使用する LLM", provider),
        field("Ollama URL", url),
        field("モデル", model, h("datalist", { id: "models" }, models.map((m) => h("option", { value: m })))),
        h("p", { class: "muted" }, "Ollama に接続できないときはエラーを表示します。デモ LLM や外部 AI へ自動で切り替えることはありません。"),
        h("button", { onclick: () => guard(async () => {
          await api("/api/settings", { method: "PUT", body: { llm_provider: provider.value, ollama_url: url.value, ollama_model: model.value } });
          toast("保存しました。"); navigate(); refreshBadges();
        }) }, "保存")),
      h("div", { class: "card" }, h("h3", {}, "表示（このブラウザに保存）"),
        field("テーマ", theme),
        field("アクセントカラー", swatches),
        field("文字サイズ", font),
        h("label", { class: "row" }, sidebar, "サイドバーを表示"))));
  },
};

// ---- ルーティング ----------------------------------------------------------
let currentPage = null;
async function navigate() {
  const name = location.hash.slice(1) || "dashboard";
  const page = pages[name] || pages.dashboard;
  // 同じ画面の再描画（承認・絞り込みなど）は動かさない。画面を移ったときだけ入れ替えの動きを付ける
  const moved = currentPage !== page;
  currentPage = page;
  document.querySelectorAll(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  $("#page-title").textContent = page.title;
  $("#page-sub").textContent = page.sub || "";
  let leaving = null;
  if (moved && !reducedMotion.matches) {
    $(".topbar").animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
      { duration: 500, easing: EASE_OUT });
    const old = $("#page").querySelectorAll(".enter");
    old.forEach((el) => el.classList.add("leave"));
    if (old.length) leaving = sleep(140);  // 前の画面が消えるのを待つ間に、次の画面を組み立てる
  }
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
  await leaving;
  $("#page").replaceChildren(root);
  if (moved) { window.scrollTo({ top: 0, behavior: "instant" }); animateIn(root); }
  // 画面に入ってから行う処理（チャットを最下部まで送るなど）。DOM に入る前に実行すると効かない
  page.mounted?.();
  page.mounted = null;
}

// 数が変わったときだけ、バッジを軽く弾ませる
function setBadge(el, n) {
  const v = n ? String(n) : "";
  if (el.textContent === v) return;
  el.textContent = v;
  if (v && !reducedMotion.matches) {
    el.animate([{ transform: "scale(.5)" }, { transform: "scale(1.18)", offset: .55 }, { transform: "scale(1)" }],
      { duration: 520, easing: EASE_OUT });
  }
}

async function refreshBadges() {
  try {
    const st = await api("/api/status");
    setBadge($("#badge-approvals"), st.pending_approvals);
    setBadge($("#badge-proposals"), st.pending_proposals);
    $("#llm-pill").replaceChildren(h("span", { class: "dot " + (st.llm.ok ? "ok" : "bad") }),
      h("span", {}, st.llm.provider === "demo" ? "デモ LLM" : st.llm.model, h("br"), st.llm.ok ? "接続中" : "未接続"));
  } catch { /* サーバー停止中など */ }
}

$("#toast").addEventListener("click", hideToast);
$("#toggle-sidebar").addEventListener("click", () => {
  const ui = loadUi(); ui.sidebarHidden = !ui.sidebarHidden; saveUi(ui); applyUi(ui);
});
window.addEventListener("hashchange", navigate);
applyUi();
navigate();
refreshBadges();
setInterval(refreshBadges, 15000);
