# StorySprout 精简版重构 · 交接 / 续做指南

> 上一轮做到哪、下一轮从哪接。配合 `docs/superpowers/specs/2026-07-22-storysprout-simplify-design.md` 一起读。

## 环境（重要）
- **专属 conda 环境**：`picture_book_generator`（Python 3.12）。解释器：
  `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python`
- 跑测试：`<那个python> -m pytest -q`（应 **237 passed, 0 failed, 0 errors**）
- 前端 typecheck：`cd frontend && npx tsc --noEmit`（应无输出=干净）
- 分支：`refactor/deepseek-gcs-vercel`（**19 次本地提交，未推、未部署**）
- 装好的依赖：pytest/dotenv/httpx/fastapi/uvicorn/python-multipart/google-cloud-storage/google-genai/pillow/reportlab/tqdm。**故意没装**：pymongo/motor/google-adk（都已从代码移除）

## ✅ 已完成（6 条需求 4 条 + 前端口令门）
1. 文字 **DeepSeek** ✅（`llm_client` 转调 `src/deepseek_client.py`）
2. 图片 **`gemini-3-pro-image`** ✅（config 默认值）
3. **去 Mongo / 只用 GCS** ✅（新 `src/core/store.py` GCS-JSON；`db.py`+`mcp_client.py` 已删；全 src 零 Mongo）
5. **门禁 → 共享口令** ✅（后端 `src/access_gate.py` 的 `AccessCodeMiddleware` 校验 `x-access-code`；前端 `AccessGate.tsx` 口令门，默认口令 `Caput Draconis`，env `ACCESS_CODE`）
6. 精简 ✅（后端已删：Mongo/MCP/ADK 批量/artist/qa/progress/agent_log/门禁4中间件/motor/google-adk）
- **app 端到端可 import、46 端点注册、237 测试全绿**

## ⬜ 剩余工作

### Plan 3 · 前端（editor 重写是大头）
1. **`frontend/src/app/editor/[bookId]/page.tsx`（1631 行）重写生成逻辑**：
   - "Gen 整章 / Gen All" → **逐页循环**：改调 `regenerateSegment(bookId, segId)` 逐段生成（后端 `POST /segment/{id}/regenerate` 能从零生成一页，含 QA+自纠+追加版本），替代**已删的** `chapter/generate` + 进度轮询
   - 删 **AgentActivityPanel**（后端 `agent-log` 已删）：删组件用法 + `frontend/src/components/editor/AgentActivityPanel.tsx` + `__tests__/AgentActivityPanel.test.tsx` + `lib/agents.ts`(AGENT_META)
   - 删 **BYOK 横幅**（约 line 1017-1042）+ `requireKey/canEdit/hasKey/keyInput` 逻辑 → 编辑器永远可编辑（`canEdit=true`）
2. **`frontend/src/lib/api.ts` 删死函数**：`generateChapter`、`getChapterProgress`、`getAgentLog`（对应端点已删）；`getConfig` 也可删（BYOK 没了；删后要清编辑器里的 `requireKey` 用法）
3. **Library 每本书加「下载 PDF」按钮**（`components/BookLibrary.tsx` → 链到 `/api/book/{id}/pdf`）
4. 依赖联动（spec §11.2 两条硬性规则）：stale 标红 + 重画=换图+版本+自动QA —— **后端已具备**（`stale-pages` 端点、`page_service.qa_and_self_correct`、`record_image_version`）；前端编辑器现有轮播/stale 逻辑基本可用，逐页重写时保留即可

### Plan 4 · 部署（Vercel + /tmp）
- `vercel.json`（Next.js 前端 + Python 短函数）
- **`/tmp` 物化**（spec §8 风险5 / 原 2b）：`GENERATED_DIR`→`/tmp`（改可配 env）；`artist`/`illustration` 读依赖图前用 `storage.localize` 从 GCS 拉（`special_pages.get_style_ref` 是范本）。注：`artist.py` 已删，逐页路径用 `illustration.generate_illustrations` —— 给它加 `localize`
- GCS 服务账号 JSON → Vercel env `GCS_SA_JSON`（代码已支持 `from_service_account_info`）
- 删 `Dockerfile`、`cloudbuild.yaml`、`.dockerignore`、`start.sh`
- `requirements.txt` 已去 google-adk；再核对 pymongo/motor/mcp 等是否残留

## 小遗留（可选清理）
- **BYOK 死管线**（inert，不影响运行）：`gemini_backend.py` 的 `set_user_api_key`/`get_user_api_key`、`generation.py` 5 处 BYOK 包裹块、`image_utils._get_client` 的 `get_user_api_key`、`helpers._require_user_key`(已 no-op)/`is_admin_token`/`book_owner_email`(已无用)
- `books.py` 里 feedback/usage 端点删了，但 `_send_owner_email`/`_format_usage_digest`/`FeedbackRequest` 等死辅助还在

## 续做建议顺序
Plan 3.1 editor 重写（先删 AgentPanel+BYOK 横幅让它变简单，再改 Gen→逐页）→ 3.2 api.ts 清理 → 3.3 Library PDF → Plan 4 部署。每步 `pytest` + `tsc --noEmit` 验证。
