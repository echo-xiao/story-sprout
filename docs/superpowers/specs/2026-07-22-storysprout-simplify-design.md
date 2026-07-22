# StorySprout — 精简版重构设计

- **日期**：2026-07-22
- **状态**：已确认，待写实现计划
- **目标读者**：本项目维护者（个人 demo / 比赛用途）

---

## 1. 目标与背景

StorySprout 现在是一个"把任意书变成儿童绘本"的应用，为 Google Cloud + MongoDB
Hackathon 而建，架构较重：Gemini 文字 + 图片、MongoDB（含 MongoDB MCP）、Google
ADK 多智能体流水线、Cloud Run + Docker 部署，外加 BYOK/admin/归属/限流四层门禁。

本次要把它**降级为一个精简的公开产品（带口令门禁）**：

1. 文字引擎换成 **DeepSeek**
2. 图片引擎用 **Gemini 最高档图像模型**（Nano Banana Pro）
3. 数据存储**只用 Google Cloud（GCS）**，彻底去掉 MongoDB
4. 部署到 **Vercel**（不再用 Cloud Run / Docker）
5. **去掉现有的复杂门禁**，改用一个共享口令
6. **代码尽量简洁**，不需要的东西直接删

用途定位：**这是一个公开产品，但带门禁**。产品页公开可访问，但**使用（生成）需通过
一个共享口令**（`Caput Draconis`）。口令发给允许使用的人。接受的风险：口令外传后，
持有者仍会用你的 key 生成（成本见 §8）。

---

## 2. 约束与关键决策

| # | 约束 | 落地决策 |
|---|---|---|
| 1 | 文字用 DeepSeek | `deepseek-chat`，OpenAI 兼容接口，所有文字生成走 `llm_client` 一个门 |
| 2 | 图片用效果最好的模型 | **`gemini-3-pro-image`**（Nano Banana Pro，GA，最高档；4K / 94% 文字准确率 / 最多 14 张参考图） |
| 3 | 只用 Google Cloud 存储 | 状态与元数据 = **GCS 里的 JSON blob**；图片 = GCS 对象。**无 MongoDB、无 Firestore** |
| 4 | 部署在 Vercel | Next.js 前端 + **Python serverless 短函数**；**无 Cloud Run、无 Docker** |
| 5 | 不要复杂门禁 | 删掉 BYOK + admin + 归属 + 限流四层；只留**一个共享口令** |
| 6 | 代码精简 | 见 §7 删除清单 |

### 2.1 为什么图片用 Nano Banana Pro 而非 Imagen 4

本应用命脉 = **角色跨页一致（喂参考图）+ 文字画进插画（云朵/对话框）+ 局部重画 +
风格化绘本（非写实）**。Nano Banana Pro 在这四点全是最强项（14 张参考图、94% 文字
渲染、localized edit、stylized 更受偏好、2–5s 快）。Imagen 4 Ultra 只在"照片级写
实人像/产品图"更强——而本应用的 `NEGATIVE_PROMPT` 明确排除写实。故选 Nano Banana Pro。

---

## 3. 目标架构

```
浏览器 (Next.js on Vercel)
   │  口令门：输入 ACCESS_CODE → 存 localStorage → 每次请求带 x-access-code
   │  逐页驱动生成：preprocess → 每页 generate（内含 自动QA + 追加版本 + ≤2 次自纠）
   ▼
Vercel Python Functions  (每个请求都短，远低于时限)
   │  DeepSeek（文字）        Gemini gemini-3-pro-image（出图 + 视觉 QA）
   ▼
Google Cloud Storage  (唯一数据层)
   ├── 元数据/状态：JSON blob（书、角色、章节、版本指针）
   └── 图片：PNG 对象（公开读，浏览器直连 storage.googleapis.com）
```

要点：
- **无常驻服务器**：所有工作拆成短请求，故不需要 Cloud Run，天然适配 serverless。
- **状态全在 GCS**：serverless 无持久磁盘，每次调用无状态；GCS 是唯一真源。
- **图片浏览器直连 GCS**：不经过函数转发，省时省流量。

---

## 4. 数据模型（GCS JSON，替代 MongoDB）

以 `book_id` 为前缀组织对象。不追求可查询性（demo 用量小），一切读写就是"取一个
JSON / 覆盖一个 JSON"。

```
gs://<bucket>/<book_id>/
  meta.json            # 书级：title, num_chapters, created_at, ...
  characters.json      # 角色列表（canonical_name, appearance, sheet_key, ...）
  segments.json        # 切段结果（chapter_idx, text, scene, ...）
  chapters/<n>.json    # 每章生成的页（title, pages[]，每页含 image_key/text）
  assets.json          # 版本指针：asset_type/asset_key → versions[] + selected_id
  images/...           # PNG 对象（角色页、场景、页图、封面、特殊页）
```

- 现在 Mongo 里的 `books / characters / book_chapters / preprocess_files /
  asset_versions` 五个集合 → 对应上面几个 JSON。
- **图片版本历史（编辑器的版本轮播）保留但简化**：版本列表存进 `assets.json`，
  逻辑不变（选择=改指针，重画=追加版本，按 hash 去重、上限封顶）。
- 一个小模块 `src/core/store.py`（新增）封装 `get_json/put_json`，替代 `db.py`。

---

## 5. 生成流程（核心变化：逐页、前端驱动）

**现在**：后台线程跑完整 ADK 流水线，前端轮询"文件是否出现"。
**改成**：前端逐页循环调用**短请求**，每步远低于 Vercel 时限，配进度条。

**关键：出图/重画是一个原子短请求，内部就把"出图→QA→版本"三件套一次做完**
（Nano Banana Pro 出图仅 2–5s，含 ≤2 次自纠也 <60s，稳落 Vercel 时限）：

| 步骤 | 端点（示意） | 内部动作 |
|---|---|---|
| 预处理 | `POST /api/preprocess` | 抓书 / TextTiling 切段 / 角色·场景抽取（DeepSeek）→ 存 GCS（可按章分批） |
| 出一页 / 重画 | `POST /api/book/{id}/page/{k}/generate` | DeepSeek 出文字 → Gemini 出图 → **自动 QA**（角色一致性，不合格自纠 ≤2 次）→ **追加版本** → 存 GCS |

首次整本 = 前端对每页循环调用上面这一个端点；ADK `SequentialAgent` 编排删除，改为
普通 Python 函数顺序调用。**任意图片重画（页/角色图/场景图/封面）都走"出图→QA→版本"
同一条路——见 §11 规则 2（硬性要求）。**

---

## 6. 门禁：单一共享口令

- 环境变量 `ACCESS_CODE`，默认值 **`Caput Draconis`**（《魔法石》格兰芬多入门暗号，
  主题呼应"进休息室=进 app"；随时可改成别的，如 `Alohomora`）。
- 前端：一个**口令门**页面，输对 → 存 localStorage → 之后每次 API 请求带
  `x-access-code` 头。
- 后端：`AccessCodeMiddleware` 对**生成类端点**校验 `x-access-code == ACCESS_CODE`，
  不符返回 403。**这是全站唯一门禁**。
- 校验必须在**后端**，否则前端拦截可被绕过、陌生人仍能烧掉你的 DeepSeek/Gemini 额度。

**定位 = 公开产品 + 共享口令门禁。** 产品页公开可访问、可被收录；但**生成类操作需口令**。
即使产品公开，没口令也生成不了、烧不到你的 key。（`noindex` 不再是隐私手段，是否收录
按需决定。）

---

## 7. 删除 / 大改 / 新增清单

### 🗑️ 整个删掉
- `src/core/db.py`（MongoDB 数据层）
- `src/core/mcp_client.py`（MongoDB MCP 客户端）
- `src/agents/adk_pipeline.py`（Google ADK 编排）
- `src/agents/progress.py`（后台进度轮询）
- `src/agents/agent_log.py`（*）+ 前端 `AgentActivityPanel.tsx`（ADK 活动面板）
- 前端 `FeedbackWidget.tsx` + 后端 feedback / Resend 邮件
- infra：`Dockerfile`、`cloudbuild.yaml`、`.dockerignore`、`start.sh`
- 依赖：`pymongo`、`motor`、`dnspython`、`mcp`、`google-adk`
- env：`MONGODB_URI`、`MONGODB_DB`、`RESEND_API_KEY`、`FEEDBACK_EMAIL_TO`、
  `ADMIN_TOKEN`、`REQUIRE_USER_KEY`

> (*) `agent_log.py` 由文件名+README 推断，实现前会先读一眼确认确实可整删，避免误删
> 仍被依赖的东西。

### ✂️ 保留但大改
- `src/app.py`：删 `BookOwnershipMiddleware` / `BYOKMiddleware` /
  `RateLimitMiddleware`；加 `AccessCodeMiddleware`。保留 `BookIdValidationMiddleware`
  （防路径穿越）+ CORS + 全局异常处理。`serve_static` 简化（图片直连 GCS 公开 URL）。
- `src/routes/helpers.py`：删 `book_owner_email` / `is_admin_token` 等门禁辅助。
- `src/llm_client.py`：文字引擎 Gemini → DeepSeek（OpenAI 兼容）；JSON 修复逻辑保留。
- `src/gemini_backend.py`：删 BYOK contextvar（`set_user_api_key` 等）与免费额度报错
  文案；只留 Gemini 图像/QA 的单一客户端。
- `src/core/storage.py`：删本地文件兜底 → **GCS-only**；GCS 鉴权改用 env 里的
  service-account JSON（`from_service_account_info`），因为 Vercel 上没有 GCP 身份。
- `src/core/provenance.py`：**保留** → 适配到 store。它管的是**文本来源**
  （preprocess/writer/user），防重画覆盖用户手改的文字——与 stale 级联**无关**（先前
  记错了，stale 另有机制，见 §11.2 规则 1 更正）。
- `src/config.py`：删 Mongo/门禁/Resend 配置；加 `DEEPSEEK_API_KEY`、`ACCESS_CODE`、
  GCS SA JSON；`GEMINI_IMAGE_MODEL = "gemini-3-pro-image"`。
- 前端 `lib/api.ts` + Create 页：删 `x-gemini-key` / `x-admin-token` / `x-user-email`
  与"填你自己的 Gemini key" UI；加 `x-access-code`。

### ➕ 新增
- `src/core/store.py`：GCS-JSON 读写（`get_json` / `put_json`），替代 `db.py`。
- `src/deepseek_client.py`（或并入 `llm_client.py`）：DeepSeek 封装。
- 后端 `AccessCodeMiddleware`：一处口令校验。
- 前端：口令门页面 + `noindex` meta。
- 根目录 `vercel.json`：Python 函数运行时 + 路由配置。

### ✅ 保留不动（改引擎不改逻辑）
`analysis/chapter_split.py`（TextTiling 切段）、`agents/analyzer|writer|artist|qa`
（核心 4 步）、`generation/character_sheet|special_pages|illustration|page_service|
gemini_consistency_check`、`renderer/pdf_export.py`（PDF）、`extraction`（含 Gutenberg
抓取）、整个前端编辑器。

---

## 8. 技术风险与对策（落地时验证，非拦路）

1. **GCS 在 Vercel 上的鉴权**：Cloud Run 靠附着服务账号自动鉴权；Vercel 无 GCP 身份。
   → 新建一个**只有该 bucket 权限**的 service account，把其 JSON 放进 Vercel env，代码
   用 `from_service_account_info` 读。
2. **Vercel Python 依赖体积**：`Pillow + google-cloud-storage + reportlab` 需压进
   250MB 解压上限。→ 落地时确认；必要时精简依赖或分函数。
3. **单页时限**：出图仅 2–5s，故"出图→QA→追加版本"合并成**一个短请求**即可稳落时限内
   （含 ≤2 次自纠也 <60s）。若个别页仍吃紧，上 Vercel Pro（300s）。
4. **DeepSeek 无视觉**：Vision QA 必须由 Gemini 做（图入、文字判断出），与"文字
   DeepSeek / 图片 Gemini"一致，不冲突。
5. **整条生成/QA/PDF 管线是本地磁盘（`GENERATED_DIR`）导向**（读的是文件路径，不是
   字节流）。serverless 无持久磁盘 → 每次函数调用需：把依赖图（角色/场景 sheet）从 GCS
   拉到 `/tmp` → 生成 → QA → 产物写回 GCS；`GENERATED_DIR` 改指向 `/tmp`。**这是 Plan 2
   最大、最险的一块**，也是"Vercel 化"真正的成本所在。

---

## 9. 非目标（YAGNI）

- 不做书归属/多租户隔离（原按 email 隔离，现全公开靠口令）。
- 不做 Firestore / 任何数据库（GCS-JSON 够用）。
- 不引入 Redis / 任何缓存层：GCS 一层兼当永久存储与结果缓存；无状态 + 客户端驱动下
  没有需缓存的服务端临时态。将来真遇并发/延迟痛点再加 Upstash Redis（YAGNI）。
- 不做后台异步任务/队列（逐页前端驱动）。
- 不做反馈收集/邮件、admin 后台、限流。
- 不做注册登录 / BYOK；门禁只靠单一共享口令，接受"口令外传=可被滥用"的风险。

---

## 10. 成功标准

1. 本地/Vercel 上：输对口令 → 贴文本或 Gutenberg 链接 → 逐页看到绘本生成 → 导出 PDF。
2. 全流程无 MongoDB、无 Cloud Run、无 Docker、无 ADK；文字全走 DeepSeek，图片走
   `gemini-3-pro-image`。
3. 陌生人拿到链接但没口令 → 生成端点 403。
4. 代码显著变小：至少删掉上面清单中的文件/依赖/env，`app.py` 中间件从 5 → 2（含新
   口令中间件）。

---

## 11. 前端流程与依赖联动

### 11.1 顶层流程（推荐排布）

口令只拦一次（存 localStorage），之后不再问；后端每个生成请求仍校验，防绕过。Library
是**枢纽**——既是"打开已有书"的入口，也是"下载 PDF"的终点，不只是终点。

```
口令门（Caput Draconis，一次）
      ↓
主页 Home ── 两个入口 ──┐
  ├─ 新建：选/传书（粘文本 · txt · Gutenberg 链接）
  │       ↓ Preprocess（切段/角色/场景，进度页）
  │       ↓
  └─ 我的书 Library（打开已有书）
          ↓ 打开
     编辑器（场景 Scenes · 角色 Characters · 页 Pages）← §11.2 联动
          ↓ 完成
     预览 / View Book ──→ 下载 PDF
          ↕ 随时可回 Library（每本书 = 继续编辑 · 预览 · 下载 PDF）
```

变化点：入口加口令门；顶部导航保留 Create / Editor / Library，删 Agent 按钮 + BYOK
横幅；生成进度改前端逐页驱动；PDF 下载在 Library 每本书 + 预览页各放一个按钮。

### 11.2 依赖联动（编辑器核心 —— 硬性要求）

> 以下两条是用户**明确强调、一定要**的行为，实现时不得省略、不得弱化。

**★ 规则 1（硬性）— 改上游图 → 下游页 stale 立即标红**

| 你改了 | → 立即标红 stale 的页 |
|---|---|
| **角色参考图**（重画 / 改外观） | 所有**含该角色**的页 |
| **场景背景图**（重画 / 改） | 所有**用该场景**的页 |
| 全书 Style Reference | 所有页 |
| 角色重命名 | 级联所有 segment 名字 + alias map + sheet 文件名（改名级联，非 stale） |

实现靠**资产时间戳**（非 provenance）：store 里每张图（角色/场景/页）都带 `updated_at`；
某页图的时间早于它依赖的角色/场景图 → 判该页 stale。（`provenance.py` 管的是文本来源，
与此无关。）

**★ 规则 2（硬性）— 重画任意一张图片 = 三件套，绑成一个原子动作**

```
重画任意图片（页 / 角色图 / 场景图 / 封面）
  → ① 换掉插画（displayed image 更新）
  → ② 追加版本（version +1，可回溯）
  → ③ 自动 QA（Vision 自检；页 = 角色一致性；不合格触发有界自纠重画 ≤2 次）
```

三步**必须同时发生**，任何重画入口都走这同一条路——不会漏 QA、不会漏版本。对应后端
§5 的单一短请求端点。

**"改页面内容" ＝ "重画该页"（同一件事，走规则 2）**
在页面里改 `characters_in_scene`（或该页任何输入）后重画，就是规则 2 这条路——
`scene_background` 只是"页输入"的一部分，重画时一并处理。**删掉原先"防抖 2s 自动重画
scene_background"的特例**（前端 `triggerSceneBackgroundRegen` + 后端
`generateSceneBackground` 那套），代码更简、也不会在每次编辑时偷偷烧钱。

> 待你拍板的小 UX：改完 `characters_in_scene` 是**点一下"重画"**触发（推荐，省钱、可
> 攒多处改动一次画），还是**改完自动重画**。默认按"手动点重画"。
