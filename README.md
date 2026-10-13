# TrueFit 简旨

> 给简历捡旨（减脂）。

## 一句话介绍

不编造、不美化，只把已有的经历，排到最该被看到的位置。

## 背景与痛点

- 简历优秀，却海投杳无音信
- 履历丰富，却要为每个岗位逐一调整，费时费力
- 多少人，因简历调整繁琐，被埋没了才华
- 现有工具要么「美化」（可能编造），要么「打分」（不解决表达）

## 核心理念

- **不编造、可溯源**——每条 AI 输出都能点回原文，用户敢投，面试敢用
- **只做减法，不新增事实**——完整原始简历是唯一事实源
- **人始终有最终决定权**——最终稿归用户，重新生成不覆盖手改内容

## 产品形态

- **输入**：完整简历（PDF / Word / Markdown / TXT / 粘贴文本）+ JD
- **输出**：瘦身简历（三层结构：核心 / 相关 / 其他）+ 匹配报告（不调 LLM）
- **两种模式**：
  - **自己给简历瘦身**（主推）：用户勾选 + 手动聚类 → 编辑页逐条整理，逐步走完 2/3/4 步
  - **一键生成**：加载页跑完全流程后**直接跳到第 5 步（完成页）**——自动确认第 2/3/4 步（默认聚类 + 默认改写 + 默认其他信息），不满意可点流程标志回溯修改（自动聚类功能开发中，一期用"每 E 一个 G"占位）

## 核心功能

| 功能 | 说明 |
|---|---|
| 四维度相关性打分 | 技能 / 内容 / 行业 / 资历，各 0-10 分，默认权重 20/45/15/10，维度可勾选、权重可调、有预设系列 |
| 三层分层 | 核心（≥7）/ 相关（4-7）/ 其他（<4），含经历质量评估（降分）与硬信号升级（加分）两条修正规则 |
| Source Mapping | 悬停高亮，瘦身简历 ↔ 原文双向对应，标注每处是照搬 / 压缩 / 合并 |
| 编辑页 | 勾选保留 / 拖拽调序 / 合并聚类 |
| 加载页 | 4 步进度列表（读取简历 / 读取职位要求 / 分析匹配度 / 整理相关经历）+ 分段进度条 + 百分比 + 剩余时间 + 实时处理日志（后端真日志，带时间戳，滚动隐藏滚动条） |
| 流程标志 | 5 步可点导航，规则统一：当前步及之前可点、之后灰色不可点；一键生成到第 5 步时前 4 步均可回溯 |
| 中栏行内编辑 | 逐句改字（改过的标 user_edited，脱离溯源）；每经历可"↻ 还原"回 AI 原始处理 |
| 导出 | Markdown / 纯文本 / 复制到剪切板 |

## 技术架构

### 总览

```
┌─────────────────┐         ┌─────────────────────┐         ┌──────────────────┐
│  index_live.html │  HTTP   │     server.py       │  内部   │  src/pipeline/   │
│   （前端单文件）  │ ──────> │  （Flask Web 服务）  │ ──────> │  （Pipeline）     │
└─────────────────┘         └─────────────────────┘         └──────────────────┘
                                     │                                │
                                     ▼                                ▼
                            data/jobs/{job_id}/              src/prompts/ (LLM prompts)
                            （每次上传独立产物）               .env (DEEPSEEK_API_KEY)
```

### 前端（`index_live.html`）

单文件（HTML + CSS + JS），包含三个视图：

- **首页**：上传（PDF/Word/TXT/MD/粘贴）+ 配置抽屉（4 维度/权重/预设）+ 两个生成入口
- **编辑页**：以 E（经历原子）为粒度，勾选 / 拖拽 / 合并 / 排序
- **结果页**：左栏原文 + 中栏瘦身简历 + 底部匹配报告 + 导出 + 行内编辑

**PDF / Word 在前端解析**（pdf.js / mammoth.js），解析结果填入预览框，用户可查看、可编辑。

### 后端（`server.py`）

Flask 服务，核心接口：

| 接口 | 作用 |
|---|---|
| `POST /api/upload` | 接收简历 + JD 文本或文件，计算指纹，启动后台任务，返回 `job_id` |
| `GET /api/job/{job_id}` | 查询任务进度（running / done / failed + progress 文案 + `logs` 实时日志数组） |
| `GET /api/result/{job_id}` | 获取最终产物（resume / jd / clusters / changes / scored / report） |
| `GET /api/cache` | 指纹缓存查询：同一简历+JD 命中则秒回历史产物 |

**缓存机制**：`job_id` 即输入指纹（resume_hash + jd_hash 的 SHA-256 截 24 位 hex）。上传时先查 `data/jobs/` 下各任务 `meta.json` 中的指纹，命中且 `status=done` 直接复用产物（不重跑 pipeline）。要强制重跑，删除或移出对应 `data/jobs/{job_id}/` 文件夹即可（改名无效，指纹存在 meta.json 里）。

**模型预热**：server 启动时后台线程预加载 bge-m3（约 20-30 秒），之后调用 3（打分）稳定在 ~6 秒，不用首个任务承担模型加载时间。

每次上传独立目录：`data/jobs/{job_id}/`，包含所有中间产物 + `meta.json`（状态 + `timings` 每步耗时）+ `logs.json`（处理日志，`{time, msg}` 格式，time 为相对开始的秒数）。

### Pipeline（6 个环节 + 2 个规则环节）

```
简历原文 + JD原文
  ↓
[调用1] 简历提炼（LLM）→ experiences JSON   ┐
                                            ├ ThreadPoolExecutor 并行执行
[调用2] JD提炼（LLM）→ requirements JSON    ┘
  ↓
[调用3] 四维度打分（规则 + embedding）→ 每条经历分数
[调用4] 分层（质量分 + 硬信号 + 阈值，纯规则）→ tier
  ↓
[构造 clusters：每个 E 一个 G]    ← 一期方案（自动聚类待开发）
  ↓
[调用5] 改写指令生成（LLM）→ changes 数组
  ↓
[环节G] Gate 1-4 校验（本地规则）→ 瘦身简历 + mapping
  ↓
[调用6] 匹配报告生成（纯规则）→ 匹配报告
```

LLM 只输出结构化指令，不输出最终文本；打分、分层、Gate、报告全部本地规则执行。详细设计见 [pipeline_design.md](docs/pipeline_design.md)。

**总耗时约 1-1.5 分钟**：调用 1、2 并行后总时长 ≈ max(简历提炼, JD提炼) + 后续串行环节；大头在简历提炼（大 JSON 输出，约 30-60 秒，随简历长度/服务响应波动）。每步实际耗时记录在 `meta.json` 的 `timings` 字段。

### 防编造机制（Gate 1-4）

| 门禁 | 检查 | 不通过处理 |
|---|---|---|
| Gate 1 | source_ids 必须真实存在 | 拒绝该条 |
| Gate 2 | 公司名 / 时间 / 职位不能被改 | 拒绝该条 |
| Gate 3 | operation 在允许列表内（7 种） | 拒绝该条 |
| Gate 4 | 数字、贡献动词、事实性名词必须在原文找到 | 回退原文 |

### 数据层

- **source_store**：原文库（切块编号，前缀 W=工作 / P=项目 / D=JD职责 / R=JD要求 / B=JD加分项）
- **mapping_table**：输出 ↔ 原文映射（output_id → source_ids + operation）
- **output_store**：输出库（含 origin 字段，区分 AI 生成 / 用户手改）

详见 [Source Mapping (自研).md](docs/Source%20Mapping%20(自研).md)。

## 项目结构

```
True-Fit简旨/
├─ index_live.html     # 前端（真产品，单文件）
├─ server.py           # 后端（Flask Web 服务）
├─ docs/               # 设计文档：算法、Pipeline、Source Mapping、产品设计、项目现状快照
├─ data/
│  ├─ original/        # 原始简历 / JD（resumes/ jds/）
│  ├─ extracted/       # 调用1/2 提炼出的结构化 JSON
│  ├─ output/          # 打分、changes、聚类、报告等产物（离线测试用）
│  └─ jobs/            # 每次上传任务的产物（{job_id}/ 独立目录）
├─ src/
│  ├─ prompts/         # 调用1/2/5 的 prompt 文件
│  └─ pipeline/        # 产品代码：llm_client、llm_providers、embedding、step1~6、gate、run_pipeline、run_upload
├─ prototype/          # 早期 HTML 原型（保留参考）
├─ tests/              # 验证脚本（打分 / Gate / 报告 / 全链路）+ 6 组测试用例
├─ notes/              # 竞品调研、草稿
├─ requirements.txt
└─ .env.example        # 复制为 .env，填入密钥
```

## 快速开始

### 环境要求

- Python 3.8+
- 依赖见 [requirements.txt](requirements.txt)（openai / python-dotenv / requests / FlagEmbedding / pypdf / python-docx 等；bge-m3 首次运行自动下载 ~2.2GB 模型）

### 安装

```bash
pip install -r requirements.txt
```

### 配置

复制 `.env.example` → `.env`，填入 `DEEPSEEK_API_KEY`。

可选：`LLM_PROVIDER=ollama` 切换本地模型（默认 qwen2.5:7b @ localhost:11434），LLM 调用不走云端。

### 启动 Web 服务

```bash
python server.py
```

服务监听 `http://localhost:8000`。

浏览器打开 `index_live.html`（或访问 `http://localhost:8000/live`）。

**说明**：`app.run(debug=False)`——改后端代码后需手动重启（Ctrl+C 后再 `python server.py`）；开发期可临时改回 `debug=True`（自动重载，但会重启进程、模型需重新预热，会污染耗时测量）。启动后控制台出现 `[warmup] bge-m3 预热完成` 即可开始使用。

**使用流程**：

1. 上传简历 + JD（支持 PDF / Word / Markdown / TXT / 粘贴文本）
2. 调整配置（可选）
3. 点【自己给简历瘦身】（加载页 → 第 2 步编辑页）或【一键生成瘦身简历】（加载页 → 直接第 5 步完成页）
4. 加载页实时显示：4 步进度 + 百分比 + 剩余时间 + 处理日志（约 1-1.5 分钟，含 LLM 调用）
5. 完成页预览瘦身简历 + 匹配报告，可导出（Markdown / 纯文本 / PDF）；一键生成不满意可点流程标志回溯第 2/3/4 步修改

**提示**：同一份简历 + JD 再次生成会命中缓存秒开；做速度测试时先清掉 `data/jobs/` 下的任务文件夹。

### 跑通离线验证（不需要 Web 服务）

```bash
python tests/run_live.py
```

## 当前状态

### 已完成

- **算法设计**：4 个维度 + 2 条修正规则，6 组测试 28 条经历，分层判断 100% 与直觉一致（见 [相关性计算算法 (自研).md](docs/相关性计算算法%20(自研).md)）
- **Pipeline**：全部环节定稿并通过验证
- **Web 产品**：`index_live.html` + `server.py` 完整跑通
- **真上传**：PDF / Word / Markdown / TXT / 粘贴文本，前端解析 + 用户可编辑
- **双模式**：自己瘦身（主推）+ 一键生成（次推）
- **编辑页**：E 粒度勾选 / 拖拽 / 合并 / 排序
- **结果页**：左栏原文 + 中栏瘦身简历 + 匹配报告 + 悬停溯源 + 行内编辑 + 导出
- **前端：5步流程**：上传 → 提取相关履历 → 确定核心经历 → 确定其他信息 → 完成
- **前端：加载页**：4 步进度列表（脉动圆点）+ 分段进度条 + 百分比 + 剩余时间三段式文案 + 实时处理日志（新日志上滑入场、最新条目光标闪烁）
- **前端：一键生成直通第 5 步**：加载页跑完自动确认第 2/3/4 步（默认聚类/改写/其他信息），直接到完成页，可回溯修改
- **前端：流程标志**：统一可点规则（当前步及之前可点、之后灰色）
- **前端：样式与分页**：样式调整（标题对齐、字号、分隔）、分页/连续查看、审阅（页内/全屏）、导出（PDF/MD/TXT）
- **前端：交互能力**：悬停高亮、匹配报告、编辑功能
- **后端：调用 1、2 并行**：ThreadPoolExecutor，总时长省一次 LLM 调用
- **后端：真日志**：pipeline 关键节点写 `logs.json`（带相对时间戳），`/api/job` 返回 `logs` 数组
- **后端：每步耗时**：`meta.json` 的 `timings` 字段记录 8 项耗时（并行步取 max）
- **后端：指纹缓存**：`/api/cache` + 上传时指纹比对，同一简历+JD 秒开
- **后端：bge-m3 预热**：启动时后台线程加载模型，调用 3 稳定 ~6 秒
- **真实 API 全链路**：DeepSeek / 本地 Ollama 均可

### 待完成

**短期**

- 进度持久化：上传中途刷新页面，进度不丢（用 sessionStorage 存 job_id）
- 上传进度条 + 取消按钮
- 报告反映用户编辑：目前报告基于原始 clusters，不反映编辑页的勾选/合并
- 剩余时间动态估算：当前加载页用固定文案（约 1 分钟 / 约 30 秒），二期基于 timings 历史数据动态估算
- 自动聚类：一键生成的核心功能（当前用"每 E 一个 G"占位）

**中期**

- 内容分标定（二期）
- Source 粒度优化（二期）
- React 重构：`index_live.html` 已 1800+ 行，维护成本上升

**长期**

- 部署上线：前端静态托管 + 后端云服务器
- 多用户支持：用户系统 / 任务队列 / 并发控制
- 成本控制：配额限制 / Ollama 本地化 / 收费

**其他已知技术债**

- embedding 相似度区间窄（4.8-6.0），区分度不足，一期靠其他维度拉开差距
- Source 粒度：从段落级（W1）到句子级（W1_L3）会更精准

详细现状、决策记录、技术债见 [docs/项目现状快照.md](docs/项目现状快照.md)。

## 路线图

- **一期（MVP）**：✅ 已完成（前端产品 + 真上传 + 双模式 + 完整编辑能力）
- **二期（体验优化）**：进度持久化、报告反映编辑、上传进度条、自动聚类
- **三期（工程优化）**：React 重构、部署上线、多用户支持
- **远期**：Employer 版本（HR 反向匹配）

## 参考项目

- Resume-Matcher（GitHub）
- resume-jd-optimizer-cn（GitHub）
- ApplyRight-AI（GitHub）

调研笔记见 `notes/GitHub_CounterPart/`。

## 许可

Copyright © 2026 王晓宇. 保留所有权利。