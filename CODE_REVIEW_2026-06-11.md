# Code Review — picture_book_generator (2026-06-11)

审查方式：端到端实测一张图片生成（`the_great_gatsby` segment 0，全链路成功）+ 后端/前端两路深度代码审查 + ruff/tsc 静态检查 + 真实数据核对。

## 端到端验证结果 ✅

`POST /api/book/the_great_gatsby/segment/0/regenerate` → 复用 character sheet → 文本简化 → Gemini 生图 → 自动 QA（100 分）→ MongoDB 同步 → 完成 marker。产物 `chapters/ch00/pages/page_001.jpg` 正常，图中 Nick Carraway 形象与嵌入文本无误。主链路是健康的。

TypeScript typecheck 干净；ruff 仅有 f-string 小问题；`.env` 已 gitignore。

---

## P0 — 高确定性，建议优先修

### 0. ☁️ 部署版所有图片生成功能失效：Cloud Run 临时磁盘 + 无 GCS 写回（2026-06-11 用户实测确认）
- 现象：部署版 characters Save & Regenerate 不出结果图、`POST /characters/{name}/quality` 404。
- 根因链：`.dockerignore` 排除 `data/generated/*`（容器启动时生成目录为空）→ 全代码库无任何 GCS 上传逻辑（仅 `app.py:124` 有读取时跳 GCS 的兜底）→ 生成的图片只写入容器实例临时磁盘 → 缩容/多实例后文件消失或不可见 → sheets 列表（`editor.py:68-75` 扫本地磁盘）为空、quality 查不到 sheet 返回 404（`generation.py:885-886`）。
- 本地开发完全正常（磁盘持久），仅部署环境失效——单元测试不可能捕获，需部署后冒烟测试。
- 修法（推荐）：GCS bucket 挂载为 Cloud Run 卷（`gcloud run services update --add-volume name=assets,type=cloud-storage,bucket=picture-book-gen-assets --add-volume-mount volume=assets,mount-path=/app/data/generated`），零代码改动；或在每个图片写入点增加 GCS 上传并把 exists/glob 检查改走 GCS。

### 1. 💰 BYOK 计费门禁漏掉 `/api/generate/upload`（两路审查独立发现）
- `src/app.py:82-85` 的 `_GEN_SUFFIXES` 不含 `/upload`，`src/routes/books.py:224` 路由本身也没有 `Depends(_require_user_key)`。
- `REQUIRE_USER_KEY=true` 时公网用户可经文件上传免 key 触发完整 preprocess，费用记到项目账上。
- 修法：`_GEN_SUFFIXES` 加 `/generate/upload`，路由加 `Depends(_require_user_key)` 兜底。

### 2. 💰 章节生成给 minor 角色无差别生成 character sheets（实测确认）
- 实测：gatsby 共 116 个角色（main 4 / supporting 7 / **minor 105**），磁盘上 116 个 sheet + 116 个 portrait 全部生成（≈210 次多余图片调用）。第 4 章宾客名单的一次性人名每个都有角色卡。
- 根因：预处理路径有过滤（`src/preprocessing/pipeline.py:588` 只取 main+supporting），但章节生成路径 `analyzer.get_chapter_characters`（`src/agents/analyzer.py:140-166`）按 `characters_in_scene` 收集、不看 role，`adk_pipeline.py:148` 全量生成。
- 修法：`get_chapter_characters` 过滤 `role in ("main","supporting")`（minor 角色在插图 prompt 里用文字描述即可，不需要参考 sheet）。

### 3. 🗄️ editor.py 四个 LLM 端点不加锁，analysis.json 并发丢更新（已验证）
- `src/routes/editor.py:684-713`（simplify）、`:716-757`（background）、`:760-795`（summarize）、`:797-864`（chat）：读整份 analysis → 数秒 LLM 调用 → 整份写回，全程无锁；期间任何 `PUT /segment/{id}` 的修改被覆盖。`update_segment`（`:590`）有 `_analysis_lock`，这四个没有。`generation.py:180-196` 的后台 `_regen` 同模式、窗口更长。
- 修法：LLM 调用放锁外；写回时持锁重读-合并-写，不写请求开始时的旧快照。

### 4. 📉 预处理断点续跑丢 `simplified_text`，QA 系统性失真（已验证）
- checkpoint 只存 7 个字段（`src/preprocessing/pipeline.py:708-719`），漏了 `:381` 写入的 `simplified_text`。中途失败重跑后，已完成章节的 simplified_text 全空，下游 QA 拿原文比对图中文字。
- 修法：checkpoint 字段加 `simplified_text`。

### 5. 🔁 前端轮询孤儿链 + stale closure（已验证）
- `AgentActivityPanel.tsx:65-77`：async 递归 setTimeout，cleanup 只 clearTimeout，in-flight 请求返回后续命 → 永不停止的孤儿轮询。修法：`cancelled` 标志（`GenerationProgress.tsx:22-67` 已是正确写法，照抄）。
- `editor/[bookId]/page.tsx:658`：5s 轮询（最长 120s）里 `refreshStale(selectedChapter)` 捕获旧章节值；应用已有的 `selectedChapterRef.current`（`:531` 的 `handleRegenerate` 已正确使用）。

### 6. 🖼️ 页面重生成 180s 超时后前端静默放弃 → 破图
- 后端 regenerate 一开始就把当前页图移入 history（`generation.py:123-137`），生成+QA 自纠常超 180s；前端超时（`page.tsx:527`）只 resolve 不提示不刷新，`<img>` 404。
- 修法：超时后继续低频轮询 `regen-status` 或提示"仍在后台生成"。

---

## P1 — 中等确定性 / 一致性问题

7. **三套页码换算并存**：`helpers.segment_page_num`（id 排序位置）vs `editor.py:556`（`id - min(ids) + 1`）vs `gemini_consistency_check.py:396-410`（列表下标+1）。segment id 一旦有空洞（删段/重分段/跳过短段），编辑器展示图、regen 目标页、consistency 报告页号三者错位。统一用 `segment_page_num`，consistency 从文件名解析页号。
8. **自纠错后同页双扩展名**：`artist.py:269` 自纠错只 copy 备份不移走旧图，新图 mime 翻转时 pages/ 同时有 `page_001.png`（旧）+ `.jpg`（新），所有 `for ext in (.png,.jpg)` 读取点取到旧图，`glob("page_*.*")` 把进度计成 2 页。对齐 `page_service.qa_and_self_correct` 的 move 语义。
9. **角色 sheet 重生成不动 portrait**：`generation.py:917-922` 只移 sheet，`character_sheet.py:183-186` 复用旧 portrait 并作为 "Match EXACTLY" 参考喂回 → 用户改外观后重生成基本无效。portrait 应一并归档。
10. **regen marker 非原子写 + 裸 json.loads**：`generation.py:275,303`（write_text）+ `:325`（无 try/except），前端高频轮询读到半写文件直接 500。复用 `progress.py:39-43` 的 temp+rename 原子写。
11. **一页 simplify 失败 → 整章崩溃**：`text_simplifier.py:204` 直接 raise，链路无人捕获，整章标 failed。单页失败应回退 scene_summary/原文并 warning。
12. **alicloud 路径残留模糊名匹配**：`illustration.py:218-221` 子串匹配（Gemini 路径已因 Defarge 串扰修为精确匹配 `:59-67`），test 环境会注入错误角色 sheet。
13. **Mongo 优先读遮蔽磁盘新数据**：`helpers.py:44-61` Mongo 优先、`:94-108` Mongo 写失败仅 warning → Mongo 抖动期间的编辑在恢复后"静默回滚"。读取时比较 `updated_at`/mtime 取新者。
14. **simplify/chat 改文本不失效 quality 缓存**：`editor.py:684-713`、`:854-862` 缺 `update_segment`（`:603-614`）已有的缓存失效逻辑。
15. **stale-pages 全靠 mtime**：批量重写 sheets（实测 6/10 19:23 全量 touch）会把全书页面标 stale。建议 sheet 内容 hash 或只在真正重生成时 touch。
16. **`regenerate_special_page` 不校验 page_type**：`generation.py:680-766` 非法值只在后台 log，接口返回 generating，前端永远轮询不完。入口白名单校验 + 400。
17. **book_id 碰撞与保留名**：同首行/同文件名的书互相覆盖；book_id="uploads" 时 `DELETE /api/book/uploads` 会删掉所有人的上传文件（`books.py:204,210-244`）。id 加短 hash + "uploads" 入保留名。
18. **`check_chapter_consistency` 请求内同步跑 N+1 次视觉调用**：30 页必超 600s TimeoutMiddleware，客户端 504 但调用继续烧配额。改 background task + 轮询（项目已有现成模式）。
19. **重复提交同名书 → 假完成跳转**：`GenerationProgress.tsx:44-54` 见旧 `chapter_segments.json` 即判 complete 跳编辑器，新 preprocess 才刚启动。以 `preprocess/progress` 的 status 为准。
20. **handleSave 静默失败 + 整体清空 dirty 集合**：`page.tsx:473-477` 保存失败无提示；保存期间的新编辑标记被一并清掉，切章节不再确认 → 编辑悄悄丢失。失败 alert；按 id 逐个删 dirty。

## P2 — 改进建议（摘选）

- **成本**：Gemini 调用无请求级 timeout（挂起占死线程池，marker 永不出现）；参考图全尺寸 base64 每页重编码重传（最多 5 sheet+style+scene），应缩图+缓存；visual_details 逐角色串行补全应批量；preprocess/章节硬超时（600s/900s）应随规模动态化。
- **联动**：章节完成后 specialPages 不刷新（绿点要手动刷新）；view-only 模式多个按钮未 disabled（点了 403 且只进 console）；VersionsCarousel 选版本仅本地预览、无 restore 端点、刷新即弹回；SceneManagement "Gen All" 一次性并发全部场景必撞免费 tier 限流（CharacterManagement 已是逐个等待，应对齐）；整章重跑后 IllustrationPanel 主图 cache key 不变 → 显示旧缓存。
- **硬编码**：sheet prompt 写死 "HUMAN only / Historical period clothing"（`character_sheet.py:89-91`、`illustration.py:157`），动物主角/现代背景的书会系统性画错。
- **清理**：死代码契约漂移——`listBooks()` 类型与后端投影不符、`chatWithAI`/`deleteBook`/consistency wrapper 无 UI 调用、`types/index.ts` 旧 `/api/status` 类型残留、`CharacterManagement.handleSave` 死代码；`delete_book` 漏清 `steps` 集合；`step_logger` 进程内计数重启后覆盖旧文件。

## 测试建议（当前前后端均为零测试）

后端（pytest，按性价比排序）：
1. 页码不变量：`segment_page_num` × `build_scenes` × editor URL 三者对带空洞 id 的 segments 必须一致（锁定 #7）
2. `qa_and_self_correct` 文件状态机（mock QA + regen_fn）：失败恢复备份 / 新分低还原 / 扩展名翻转（锁定 #8）
3. BYOK 门禁矩阵：`_GEN_SUFFIXES` × 全部生成路由参数化（立刻抓出 #1）
4. preprocess checkpoint 往返：写→恢复→字段集相等（抓出 #4）
5. `generate_json` JSON 修复链、`_normalize_page_quality` 脏输入、simplify 页码合并

前端（vitest + fake timers）：
1. 契约测试：用 FastAPI `/openapi.json` 校验 `lib/api.ts` 全部 wrapper（或 openapi-typescript 生成客户端）
2. 轮询生命周期：组件卸载后不再发请求（锁定 #5）；regen-status complete/error/timeout 三态收敛
3. dirty 集合：保存成功/失败/保存期间再编辑（锁定 #20）
4. cache key 回归：同 URL 重生成必须 bump（锁定 P2 缓存项）

## 总体评价

主干链路（preprocess → sheets → simplify → illustrate → QA → self-correct）设计合理且实测可跑通，代码注释显示历史修 bug 意识强。最大的系统性风险是：**同类问题只修了一处分身**（名字模糊匹配、move-vs-copy、原子写、缓存失效、role 过滤、页码换算都各有"修过的"和"漏掉的"副本），而零测试让这种漂移无法被拦截。优先级：#1（费用安全）→ #3（数据丢失）→ #2（成本）→ #4（QA 失真）→ 前端 #5/#6。
