# TrueFit 简旨

> 给简历捡旨（减脂）。

## 一句话介绍

不编造、不美化，只把已有的经历，排到最该被看到的位置。

## 背景与痛点

- 劳资市场紧张：企业招不到人，求职者找不到工作
- 匹配的人因为简历陈述方式错失机会
- 现有工具要么「美化」（可能编造），要么「打分」（不解决表达）

## 核心理念

- **只做减法，不新增事实**——完整原始简历是唯一事实源
- **一切可溯源**——每条 AI 输出必须绑定 source_ids + 操作类型
- **人始终有最终决定权**——最终稿归用户，重新生成不覆盖手改内容

## 产品形态

- **输入**：完整简历（PDF / Word / Markdown / TXT / 粘贴文本）+ JD
- **输出**：瘦身简历（三层结构：核心 / 相关 / 其他）+ 匹配报告（不调 LLM）
- **两种模式**：
  - **自己给简历瘦身**（主推）：用户勾选 + 手动聚类 → 编辑页逐条整理
  - **一键生成**：系统自动选择 + 自动聚类（自动聚类功能开发中，一期用"每 E 一个 G"占位）

## 核心功能

| 功能 | 说明 |
|---|---|
| 四维度相关性打分 | 技能 / 内容 / 行业 / 资历，各 0-10 分，默认权重 20/45/15/10，维度可勾选、权重可调、有预设系列 |
| 三层分层 | 核心（≥7）/ 相关（4-7）/ 其他（<4），含经历质量评估（降分）与硬信号升级（加分）两条修正规则 |
| Source Mapping | 悬停高亮，瘦身简历 ↔ 原文双向对应，标注每处是照搬 / 压缩 / 合并 |
| 编辑页 | 勾选保留 / 拖拽调序 / 合并聚类 |
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

Flask 服务，3 个核心接口：

| 接口 | 作用 |
|---|---|
| `POST /api/upload` | 接收简历 + JD 文本或文件，启动后台任务，返回 `job_id` |
| `GET /api/job/{job_id}` | 查询任务进度（running / done / failed + progress 文案） |
| `GET /api/result/{job_id}` | 获取最终产物（resume / jd / clusters / changes / scored / report） |

每次上传独立目录：`data/jobs/{job_id}/`，包含所有中间产物 + `meta.json`（状态）。

### Pipeline（6 个环节 + 2 个规则环节）

```
简历原文 + JD原文
  ↓
[调用1] 简历提炼（LLM）→ experiences JSON
[调用2] JD提炼（LLM）→ requirements JSON
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

**总耗时约 90 秒**（大头在调用 5：LLM 生成 8 条 change，约 60 秒）。

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

**使用流程**：

1. 上传简历 + JD（支持 PDF / Word / Markdown / TXT / 粘贴文本）
2. 调整配置（可选）
3. 点【自己给简历瘦身】（主推）或【一键生成瘦身简历】
4. 等待约 90 秒（含 LLM 调用）
5. 拿到瘦身简历 + 匹配报告，可导出

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
- **真实 API 全链路**：DeepSeek / 本地 Ollama 均可

### 待完成

**短期**

- 进度持久化：上传中途刷新页面，进度不丢（用 sessionStorage 存 job_id）
- 上传进度条 + 取消按钮
- 报告反映用户编辑：目前报告基于原始 clusters，不反映编辑页的勾选/合并

**中期**

- 自动聚类：一键生成的核心功能（当前用"每 E 一个 G"占位）
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