# Picture Book Generator

任意一本书 → 儿童绘本。自动分析书的结构和故事线，生成适合 2-6 岁儿童的绘本，包含简化文字 + AI 插图 + 可翻页 Web UI。

Google Cloud Rapid Agent Hackathon 参赛项目。截止 2026-06-11。
Hackathon: https://rapid-agent.devpost.com/

## 核心价值

传统绘本创作：3-6 个月，$10,000-50,000。
这个工具：3-5 分钟，接近 $0。

父母可以把任何书变成给孩子看的绘本 — 经典文学、科普读物、甚至"教小朋友上厕所"这种生活场景。

## 用户交互流程

```
1. 用户上传一本书（PDF/EPUB/文本）
2. 选择目标年龄段（2-4岁 / 4-6岁）
3. 选择绘本页数（8/10/12页）
4. （可选）指定想保留的章节/角色/主题
5. 等待 3-5 分钟
6. 获得可翻页的绘本，可导出 PDF
```

## 技术架构

```
用户上传书籍
    ↓
┌─────────────────────────────────┐
│  Layer 1: 文本提取               │
│  PyMuPDF (PDF) / ebooklib (EPUB) │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│  Layer 2: 结构分析（传统 NLP/ML） │  ← 不用 LLM，省 token，更精准
│                                  │
│  2a. 章节切分                    │  TextTiling + 规则（标题、换行、长度）
│  2b. 角色识别 + 关系图           │  spaCy NER + 共现矩阵 + 聚类
│  2c. 情感曲线                    │  逐段 sentiment scoring + 峰值检测
│  2d. 视觉具象度打分              │  名词/动词提取 + concreteness 评分
│  2e. 文本复杂度评估              │  Flesch-Kincaid + 词频分析
│  2f. 关键事件提取                │  TextRank + 实体-动作三元组 + importance 打分
│  2g. 角色档案生成                │  外貌描述句检测 + 对话分析 + 行为模式
│                                  │
│  输出：结构化 JSON               │
│  {章节, 角色, 情感曲线,          │
│   关键事件, 视觉评分, 复杂度}    │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│  Layer 3: 创作决策（Gemini Agent）│
│                                  │
│  3a. 场景选择                    │  基于 importance + visual_score + 情感覆盖
│      → 从 n 章压缩到 k 页        │  保留因果链完整性
│      → 绘本模板约束              │  intro → problem → attempts → climax → resolution
│                                  │
│  3b. 文本简化                    │  每页 20-50 字，句子 ≤ 10 词
│      → 按年龄段调整复杂度        │  2-4岁：极简 / 4-6岁：稍复杂
│      → 保留情感力量              │  不只是缩写，是改写
│                                  │
│  3c. 角色设定                    │  基于 2g 的角色档案
│      → character sheet prompt    │  外貌、表情、比例、配色、标志性特征
│                                  │
│  3d. 插图 prompt 生成            │  场景 + 角色 + 情感色调 + 构图指令
│      → 风格锁定                  │  统一画风（如 watercolor children's illustration）
│      → 色调规则                  │  开心=暖色 悲伤=冷色 紧张=深色
│                                  │
│  MCP 集成：                      │
│  - MongoDB MCP: 存储生成的绘本    │
│  - Elastic MCP: 搜索已有风格参考  │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│  Layer 4: 图片生成               │
│                                  │
│  4a. Character sheet 生成        │  第一步：生成角色设定图（正面/侧面/表情）
│  4b. 逐页插图生成                │  Gemini Imagen，引用 character sheet
│  4c. 角色一致性检查              │  CLIP 相似度，不达标重新生成
│  4d. 风格一致性检查              │  固定种子 + 详细 style prompt
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│  Layer 5: QA Pipeline            │
│                                  │
│  5a. 内容安全                    │  zero-shot 分类 + Content Safety API
│  5b. 词汇复杂度                  │  Dale-Chall readability ≤ Grade 2
│  5c. 故事覆盖率                  │  sentence embedding 匹配 ≥ 80%
│  5d. 幻觉检测                    │  实体集合差异（绘本 vs 原书）
│  5e. 情感弧线保真                │  原书 vs 绘本 sentiment 相关性 ≥ 0.7
│  5f. 图文匹配                    │  Gemini 多模态打分
│                                  │
│  不合格 → 定向重新生成           │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│  Layer 6: 排版 + 渲染            │
│                                  │
│  6a. 页面模板                    │  左图右文 / 满页图 / 跨页大图
│  6b. 文字排版                    │  字体、大小、位置
│  6c. 翻页效果                    │  CSS 3D / turn.js
│  6d. 导出 PDF                    │
└─────────────────────────────────┘
    ↓
输出：可翻页的儿童绘本（Web UI + PDF）
```

## 模仿真实绘本创作流程

| 传统流程 | 我们的自动化方案 | 对应 Layer |
|---------|----------------|-----------|
| 选题立项（年龄段、主题、教育目标） | 用户选择 | 用户输入 |
| 文本创作（故事大纲、起承转合） | NLP 提取故事骨架 + Gemini 简化 | Layer 2 + 3 |
| 角色设计（character sheet） | 自动提取角色档案 + Gemini 生成设定图 | Layer 2g + 4a |
| 分镜设计（每页画什么、视觉节奏） | importance ranking + 绘本模板 | Layer 3a |
| 绘制插图（线稿 + 上色） | Gemini Imagen 一步生成 | Layer 4b |
| 排版（文字位置、页码） | 模板系统自动排版 | Layer 6 |
| 审校 | QA pipeline 自动检查 | Layer 5 |

## 核心难点 + 解法

### 1. 角色一致性（最难）
- **问题**：同一角色在不同页的插图里长不一样
- **解法**：先生成 character sheet → 后续页 image-to-image 引用 + 极详细描述 prompt + CLIP 相似度校验 + 不达标重新生成
- **目标**：80% 一致性（业界无完美解）

### 2. 故事压缩取舍
- **问题**：200 页压缩到 10 页，丢什么保什么
- **解法**：importance 打分（sentiment_peak × entity_density × reference_count）+ 因果链完整性检查 + 绘本模板约束
- **目标**：关键事件覆盖率 ≥ 80%

### 3. 语言简化 ≠ 信息丢失
- **问题**："真正重要的东西用眼睛是看不见的" 3 岁怎么理解
- **解法**：Gemini 改写（不是缩写）+ 复杂度自动检查（Flesch-Kincaid ≤ Grade 2）+ 情感弧线对比验证简化后是否保留了情感力量

### 4. 内容安全
- **问题**：原书可能有暴力/恐怖/成人内容
- **解法**：Layer 2 就拦截（zero-shot 分类 + 关键词词典）→ 标记风险段落 → 跳过或软化处理

### 5. 风格一致
- **问题**：不同页的画风不统一
- **解法**：固定 style prompt（如 "watercolor children's book, soft pastel colors, rounded shapes"）+ 固定随机种子 + 统一调色板

### 6. 不同书的泛化性
- **问题**：小说、散文、科普、诗歌结构完全不同
- **Hackathon 策略**：MVP 只支持叙事类（小说/童话），用公版书 demo（小王子、爱丽丝）
- **未来扩展**：科普（概念可视化）、诗歌（意境插图）、漫画形式

## 技术栈

| 层 | 技术 |
|---|------|
| 文本提取 | PyMuPDF, ebooklib |
| NLP 分析 | spaCy, NLTK, TextBlob, KeyBERT, sentence-transformers |
| ML | scikit-learn (clustering, TextRank), scipy (peak detection) |
| Agent | Google Cloud Agent Builder, Gemini API |
| 图片生成 | Gemini Imagen |
| MCP | MongoDB MCP Server |
| 后端 | FastAPI, Redis |
| 前端 | React/Next.js, turn.js (翻页效果) |
| 部署 | Google Cloud Run |

## 文件结构

```
picture_book_generator/
├── README.md
├── requirements.txt
├── src/
│   ├── extraction/          # Layer 1: 文本提取
│   │   ├── pdf_parser.py
│   │   ├── epub_parser.py
│   │   └── text_input.py
│   ├── analysis/            # Layer 2: NLP 分析
│   │   ├── chapter_split.py
│   │   ├── character_extract.py
│   │   ├── sentiment_curve.py
│   │   ├── visual_score.py
│   │   ├── complexity.py
│   │   ├── key_events.py
│   │   └── character_persona.py
│   ├── agent/               # Layer 3: Gemini Agent
│   │   ├── scene_selector.py
│   │   ├── text_simplifier.py
│   │   ├── illustration_prompter.py
│   │   └── mcp_server.py
│   ├── generation/          # Layer 4: 图片生成
│   │   ├── character_sheet.py
│   │   ├── illustration.py
│   │   └── consistency_check.py
│   ├── qa/                  # Layer 5: 质量检查
│   │   ├── safety_check.py
│   │   ├── coverage_check.py
│   │   ├── hallucination_check.py
│   │   └── readability_check.py
│   └── renderer/            # Layer 6: 排版渲染
│       ├── layout_engine.py
│       └── pdf_export.py
├── frontend/                # Web UI
│   ├── src/
│   └── public/
├── data/
│   ├── sample_books/        # 测试用公版书
│   └── generated/           # 生成的绘本
├── tests/
└── scripts/
    └── run_pipeline.py      # 端到端运行脚本
```

## 11 天开发计划

```
Day 1  (6/1):  项目搭建 + Layer 1 文本提取
Day 2  (6/2):  Layer 2a-2c（章节切分 + NER + 情感曲线）
Day 3  (6/3):  Layer 2d-2g（视觉评分 + 复杂度 + 关键事件 + 角色档案）
Day 4  (6/4):  Layer 3（Gemini Agent: 场景选择 + 文本简化 + 插图 prompt）
Day 5  (6/5):  Layer 3 续（MongoDB MCP 集成）+ sktime 社区会议
Day 6  (6/6):  Layer 4（character sheet + 插图生成 + 一致性检查）
Day 7  (6/7):  Layer 5（QA pipeline）+ 端到端联调
Day 8  (6/8):  Layer 6（前端 UI：翻页绘本）
Day 9  (6/9):  部署 + 第二本书测试
Day 10 (6/10): 录 3 分钟 demo 视频 + 写文档
Day 11 (6/11): 提交 Devpost
```

## 评判标准对应

| Hackathon 评判标准 | 我们的亮点 |
|-------------------|----------|
| 技术实现质量 | 6 层 pipeline，传统 NLP + LLM 混合架构，不是"全扔给 LLM" |
| 用户体验设计 | 可翻页绘本 UI，上传 → 等待 → 阅读的流畅体验 |
| 潜在影响范围 | 每个家长都能用，教育场景 + 亲子阅读 |
| 创意与独特性 | "任意一本书变绘本"是全新概念，不是从零生成 |

## 未来扩展

- 漫画形式输出（多格漫画而不是绘本）
- 科普书支持（概念可视化）
- 多语言（中英日韩）
- 用户自定义角色外貌
- 家长可以把孩子的照片放进绘本（个性化）
- 语音朗读（TTS）
