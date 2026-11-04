# アーキテクチャ（Teams 特化・コアロジックのデモ版）

## 全体構成

```
ブラウザ（127.0.0.1:8765）
  │  REST（X-Teacher-Agent ヘッダー必須・Host 検査）
  ▼
FastAPI (app/main.py) ── LocalOnlyMiddleware (app/security.py)
  │
  ├─ agents/        MainAgent ─ 振り分け ─▶ SecretaryAgent（要約・議事録・返信案）
  │                  │  AgentContext（読み取り＋「提案」だけができる道具一式）
  │                  ▼
  ├─ policy/        ActionGateway.propose() ──▶ 承認キュー(approvals)
  │                 ApprovalService.approve()  ← 教員の操作（API）からのみ
  │                   ├ 内容ハッシュ照合（承認後の改変を検知）
  │                   ├ 禁止事項の再チェック（ProhibitionStore）
  │                   └ 実行 → TeamsAdapter.send_message()
  ├─ teams/         TeamsAdapter（抽象） ← MockTeamsAdapter（現在）／本物の Teams（将来）
  ├─ llm/           OllamaClient ／ DemoLLM（設定で明示的に選択。自動切替なし）
  ├─ memory/        MemoryStore（SQLite + ローカルベクトル検索）
  ├─ improvement.py 修正・評価 → 改善案 → 教員承認 → 記憶へ反映
  ├─ audit.py       操作履歴（本文などの機密情報は記録しない）
  └─ db.py          SQLite（data/teacher_agent.db）
```

## 権限制御の考え方

LLM のプロンプトには頼らず、**コードの構造**で強制する。

1. **エージェントは送信できない**：`AgentContext` には `ActionGateway`（提案のみ）しか入っていない。
   `ApprovalService`・禁止事項の書き込み・設定の書き込み・改善案の承認は、エージェントからは呼べない。
2. **許可リスト方式**：`policy/approvals.py` の `ACTIONS` に無い操作は拒否する。payload は
   pydantic で厳密に検証する（余計なフィールドは拒否。送信者は常に設定上の教員で、AI は指定できない）。
3. **承認は内容に紐付く**：承認 API は画面に表示した内容の SHA-256 ハッシュを要求する。
   修正するとハッシュが変わるので、再承認が必要になる。
4. **禁止事項は 2 回チェック**：提案時（該当したら `blocked`）と実行直前（承認後に追加されたルールにも対応）。
   NFKC 正規化と大文字小文字の同一視で、表記ゆれによるすり抜けを防ぐ。
5. **送信先はデータから決める**：未回答の質問への返信先は Teams のデータから決める。LLM の出力では決めない。
6. **外部データは信頼しない**：Teams のメッセージは `<untrusted_data>` で囲んで LLM に渡す（補助的な対策）。
   仮に LLM が注入された指示に従っても、上の 1〜5 で止まる（`tests/test_agent.py::test_injection_cannot_send_or_change_rules`）。
7. **重複実行の防止**：同じ起点（`origin`）の提案が処理中・実行済みなら作らない。承認時は
   `status='pending'` を条件にした UPDATE で二重実行を防ぐ。

## データ設計（SQLite）

| テーブル | 用途 |
|---|---|
| `settings` | LLM 種別・Ollama URL・モデル名など（再起動後も保持） |
| `teams_users` / `teams_conversations` / `teams_messages` | モック Teams |
| `assistant_messages` | 教員 ⇄ AI の会話と評価 |
| `memories` | 記憶（種別・内容・出典・埋め込み・登録/更新日時） |
| `approvals` | 承認キュー（操作・payload・ハッシュ・予測・状態・AI の元の文面） |
| `prohibitions` | 禁止事項（禁止語・送信先・操作） |
| `feedback` / `proposals` | 自己改善（修正・評価 → 改善案） |
| `audit_log` | 操作履歴 |

記憶の種別：基本・知識・エピソード・判断・嗜好・改善。

## 処理フロー例：「学生からの未回答の質問を確認して」

1. `MainAgent.route()` が `unanswered` と判定し、`SecretaryAgent` に任せる。
2. モック Teams から「学生の発言の後に教員が返信していない」個人チャットを集める。
3. 質問ごとに、記憶（知識・判断・嗜好）を検索して LLM で返信案を作る。
4. `ActionGateway.propose("teams.send_message", …)` で承認キューに入れる（禁止事項に該当したら `blocked`）。
5. 教員が承認待ち画面で確認・修正して承認すると、ハッシュ照合と禁止事項の再チェックを経て送信する。
6. 修正していれば `feedback` に記録する → 改善提案画面で改善案を生成 → 承認すると嗜好記憶に入り、次回の返信案に反映される。

## 本物の Teams につなぐとき

`teams/base.py` の `TeamsAdapter` と同じメソッドを持つアダプターを作り、`services.py` で差し替える。

- **Graph API（本人の委任権限）**：`Chat.Read` / `ChatMessage.Send`。本人名義で送信できる。
  テナントのユーザー同意設定によっては管理者同意が必要。
- **Teams Bot（Microsoft 365 Agents SDK）**：組織のアプリカタログへの登録が必要。
  返信は Bot 名義になる。
