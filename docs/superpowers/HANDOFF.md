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

## ✅ Plan 3 · 前端（editor 重写）已完成 [2026-07-22]

计划：`docs/superpowers/plans/2026-07-22-storysprout-plan3-editor-rewrite.md`（subagent-driven 执行，逐任务评审通过）。
验证：前端 `tsc --noEmit` 干净 + `npm test` 21/21；后端 `pytest` 237 passed。

1. **editor 重写**（`app/editor/[bookId]/page.tsx`，净减 ~680 行）：
   - "Gen 整章 / Gen All" → **逐页循环**：新增 `lib/pageGen.ts` 编排器（`generateOnePage`/`generatePagesSequential`，8 单测），loop `POST /segment/{id}/regenerate` → 轮询 `regen-status`；批量**只补缺失 + 重画 stale**（跳过已好的页，省钱），带协作式 **Stop**；`handleRegenerate` 复用同一编排器（DRY）
   - 删 **AgentActivityPanel** 组件 + 测试 + 编辑器用法；删死 api 函数 `generateChapter`/`getChapterProgress`/`getAgentLog`；删 `lib/progress.ts` + 测试
   - 删 **BYOK 横幅** + `requireKey/canEdit/hasKey/keyInput` → 编辑器永远可编辑
   - 删 **场景背景 2s 防抖自动重画**（`triggerSceneBackgroundRegen` 那套）；**保留**用户手动点的「Generate 场景描述」按钮（+ `generateSceneBackground` api/后端路由）
   - stale 联动保留（每次重画后 `refreshStale`；出图→版本→QA 由后端单页端点完成）
2. **Library 每本书「下载 PDF」按钮** → `<a download>` 直连 `/api/book/{id}/pdf`（read，不过口令门禁，无需 header）

### ⚠️ 对原 HANDOFF 的两处修正（实现时验证发现）
- **`lib/agents.ts` 未删**（原 HANDOFF 说删）：`AGENT_META`/`PREPROCESS_STEPS` 仍被 live 的 preprocess UI（`GenerationProgress.tsx`、`PreprocessLoadingScreen.tsx`）使用。只删了编辑器里的 `AGENT_META` 用法。
- **`getConfig` 未删、Create 页 BYOK 未清**：`getConfig` 仍被 `UploadForm.tsx`（Create 页 BYOK）使用，非死代码。**推迟**到后续 Create-页任务里一起清（删 getConfig + Create 页那块 Gemini-key UI + `requireKey`）。
- 批量循环**只出页、不出封面**：单页端点不产封面；封面仍走现有单项 regenerate（`handleRegenSpecial`，未动）。

## ⬜ 剩余工作

### Create 页 BYOK 清理（Plan 3 后段，小）
- `UploadForm.tsx` 删「填你自己的 Gemini key」UI + `requireKey`/`getConfig` 用法；随后从 `lib/api.ts` 删 `getConfig`（spec §7）。

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
Plan 3 editor 重写 ✅ 已完成 → 下一步：Create 页 BYOK 清理（小）→ Plan 4 部署（/tmp 物化是最险的一块）。每步 `pytest`（237）+ `tsc --noEmit` 验证。

> 手动冒烟（本轮**未跑**，因需 DEEPSEEK/GCS 密钥且会产生真实付费调用）：开编辑器 → 章节 Gen 逐页填充、Stop 可中断、重跑跳过已好页；改角色 → Save & Regen 重画；Library → 下载 PDF。编排器已单测覆盖，接线经 tsc + 评审验证；付费端到端留给你手动 QA。
