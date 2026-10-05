Source Mapping 是 TrueFit 的**信任基础设施**。

- 对用户：证明“这还是我的简历”
- 对产品：证明“AI 没有编造”
- 对你自己：所有功能（悬停高亮、匹配报告、改动说明）都靠它支撑

**本质不是“映射技术”，而是一套“以结构化差异为契约、以本地校验为护栏”的约束机制。**
核心设计：让 LLM 输出**带 source_ids 的改写指令**，后端用本地代码执行，从源头杜绝幻觉。

---

## 一、Source Mapping 回答什么问题？

用户悬停时，系统要能回答：

1. 这句瘦身后的内容，**来自原文哪几句**？
2. 是**原文照搬**，还是**压缩改写**，还是**合并多段**？
3. 改写的依据是什么（换关键词、调语序、去冗余）？
4. 有没有**新增**任何原文没有的信息？（必须能证明没有）

---

## 二、Source Mapping 实现

### 1. 生成流程（三步）

**核心原则：必须在生成时就埋好映射，不能后补。**
因为 LLM 输出的是文本，原文也是文本，中间没有天然对应关系。

**Step 1：给原始简历切块并编号**

把完整简历拆成最小语义单元（句子级 / bullet 级），每块给一个 ID：

```
W1_L1: 2021.07 - 至今  杭州某电子商务有限公司  后端开发工程师
W1_L3: 参与订单中心的重构工作，把原来一个比较大的单体服务拆成了订单创建、订单查询、订单状态流转几个模块...
P1_L5: 主要工作：定义了订单状态机，把订单状态流转统一收口到一个服务里...
```

**编号前缀规则：**

| 前缀 | 含义 | 适用章节 |
|---|---|---|
| W | Work | 工作经历 |
| P | Project | 项目经历 |
| D | Duty | 岗位职责（JD） |
| R | Requirement | 任职要求 / 任职资格（JD） |
| B | Bonus | 加分项 / 优先条件（JD） |

**粒度要求：** 句子级或 bullet 级（不是段落级，否则悬停无法精确定位）。

**Step 2：让 LLM 输出结构化差异，而不是纯文本**

不要让它直接写简历，而是让它输出“带来源标注的条目”：

```json
{
  "changes": [
    {
      "output_id": "out_017",
      "operation": "merge",
      "source_ids": ["W1_L3", "P1_L5"],
      "text": "参与订单中心重构，将单体服务拆分为订单创建、查询、状态流转模块；定义订单状态机统一收口状态流转并实现幂等处理。",
      "reason": "合并 W1_L3 与 P1_L5，突出重构成果"
    }
  ]
}
```

**核心好处：** 模型的每一次改写都留下痕迹，可追溯、可审核、可展示。

**Step 3：本地执行 + 四道门禁**

LLM 输出的 changes 不是最终文本，要经过本地校验才能执行。

| 门禁 | 检查 | 不通过处理 |
|---|---|---|
| **Gate 1：白名单** | source_ids 必须在 Source Store 中存在 | 拒绝该条 |
| **Gate 2：黑名单** | 禁止修改的字段（公司名、时间、职位）未被触碰 | 拒绝该条 |
| **Gate 3：可解析** | operation 在允许列表内，text 可解析 | 拒绝该条 |
| **Gate 4：原文匹配** | text 中的数字、贡献动词、事实性名词能在 source_ids 对应原文中找到 | **回退原文**或拒绝 |

**Gate 4 细化实现：**

```python
def gate4_validate(text, source_raws):
    # 1. 数字比对（带上下文，不孤立比对）
    num_pattern = re.compile(r'\d+(?:\.\d+)?\s*(?:ms|s|min|h|%|起|单|条|人|万|倍)?')
    text_nums = set(num_pattern.findall(text))
    source_nums = set()
    for raw in source_raws:
        source_nums.update(num_pattern.findall(raw))
    for n in text_nums:
        if n.replace(' ', '') not in {s.replace(' ', '') for s in source_nums}:
            return False, f"新增数字/指标「{n}」，原文不存在"

    # 2. 贡献动词门槛（带词边界，排除"不负责"）
    VERB_LEVELS = {"支持": 1, "协助": 1, "参与": 2, "负责": 3, "推动": 4, "主导": 5}
    for verb, level in VERB_LEVELS.items():
        if re.search(rf'(?<![不]){verb}', text):
            max_allowed = max(
                (VERB_LEVELS.get(v, 0)
                 for raw in source_raws
                 for v in VERB_LEVELS
                 if re.search(rf'(?<![不]){v}', raw)),
                default=0
            )
            if level > max_allowed:
                return False, f"贡献动词「{verb}」超出原文允许等级（上限{max_allowed}）"

    # 3. 事实性名词（公司名、技能名）不得新增
    # 简化：对比专有名词；生产环境可用 NER 或词表
    return True, "通过"
```

**回退机制：** Gate 4 不通过 → 该条 change 回退为 `verbatim`（原文照搬），并记录日志。

---

### 2. 存储（三层 + 表结构）

**第一层：原文库（Source Store）**

```json
{
  "W1_L3": {
    "raw": "参与订单中心的重构工作，把原来一个比较大的单体服务拆成了订单创建、订单查询、订单状态流转几个模块",
    "type": "experience_detail",
    "section": "工作经历",
    "exp_id": "E1",
    "company": "杭州某电子商务有限公司",
    "time": "2021.07-至今"
  }
}
```

用途：悬停时高亮原文。

**第二层：映射表（Mapping Table）**

```json
{
  "out_017": {
    "source_ids": ["W1_L3", "P1_L5"],
    "operation": "merge",
    "confidence": 0.95,
    "validation": {
      "numbers_preserved": true,
      "verb_gate_passed": true,
      "no_new_facts": true
    }
  }
}
```

用途：连接输出与原文，支持双向高亮。

**第三层：输出库（Output Store）**

```json
{
  "out_017": {
    "text": "参与订单中心重构，将单体服务拆分为订单创建、查询、状态流转模块",
    "layer": "core",
    "order": 3,
    "origin": "ai",
    "edited_text": null
  }
}
```

用途：渲染瘦身简历。

**三层通过 ID 关联：**
```
输出条目 out_017 → mapping → source_ids [W1_L3, P1_L5] → 原文库
```

**数据库表结构（SQLite / PostgreSQL）：**

```sql
-- 简历表
CREATE TABLE resumes (
  id            TEXT PRIMARY KEY,
  raw_text      TEXT NOT NULL,
  structured    JSON NOT NULL,
  version       INTEGER DEFAULT 1,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- JD表
CREATE TABLE jds (
  id            TEXT PRIMARY KEY,
  raw_text      TEXT NOT NULL,
  structured    JSON NOT NULL,
  version       INTEGER DEFAULT 1,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 原文库
CREATE TABLE source_store (
  source_id     TEXT PRIMARY KEY,
  resume_id     TEXT NOT NULL,
  raw           TEXT NOT NULL,
  type          TEXT,
  section       TEXT,
  exp_id        TEXT,
  company       TEXT,
  time          TEXT,
  FOREIGN KEY (resume_id) REFERENCES resumes(id)
);

-- 映射表
CREATE TABLE mapping_table (
  output_id     TEXT NOT NULL,
  result_id     TEXT NOT NULL,
  source_ids    JSON NOT NULL,
  operation     TEXT NOT NULL,
  confidence    REAL,
  validation    JSON,
  PRIMARY KEY (result_id, output_id)
);

-- 输出库
CREATE TABLE output_store (
  result_id     TEXT NOT NULL,
  output_id     TEXT NOT NULL,
  text          TEXT NOT NULL,
  layer         TEXT,
  sort_order    INTEGER,
  origin        TEXT DEFAULT 'ai',
  edited_text   TEXT,
  PRIMARY KEY (result_id, output_id)
);

CREATE INDEX idx_mapping_result ON mapping_table(result_id);
CREATE INDEX idx_mapping_source ON mapping_table(source_ids);
CREATE INDEX idx_output_result ON output_store(result_id);
```

---

### 3. operation 类型

| operation | 含义 | 悬停时展示 |
|---|---|---|
| `verbatim` | 原文照搬 | 直接高亮原文 |
| `compress` | 压缩冗余 | 高亮原文 + 标注“已精简” |
| `reword` | 换措辞对齐JD | 高亮原文 + 显示JD关键词 |
| `reorder` | 调整顺序 | 高亮原文 + 标注“已前置” |
| `merge` | 多段合并 | 高亮多段原文 |
| `demote` | 保留但降权 | 【相关内容】/【其他内容】 |
| `omit` | 彻底不出现 | 不展示，但可在报告里查 |

暂不加入以下操作模式，但如有需求可以考虑：

*自定义模式：用户勾选允许哪些操作 → 系统只在该权限内工作。
模型不能做用户没授权的操作。比如用户只勾 `verbatim + reorder`，那模型就只能调顺序、不能改一个字。*

*预设模式：*

| 模式   | 允许的操作                                                   | 效果             |
| ---- | ------------------------------------------------------- | -------------- |
| 原文模式 | verbatim + reorder                                      | 只调序，一字不改       |
| 精简模式 | verbatim + compress + reorder + demote                  | 只删减压缩，不换词      |
| 对齐模式 | verbatim + compress + reword + reorder + merge + demote | 默认真实向润色        |
| 激进模式 | 全部 7 种                                                  | 最大力度重构（仍不新增事实） |
| 自定义  | 用户自由勾选                                                  | —              |

---

### 4. 用户手改的处理

TrueFit 不是“生成一份匹配简历”，而是“生成一个匹配后用户可继续编辑的模板”。
（最终稿永远是用户的，AI 只是给了一个起点。）

**实现上注意两点：**

1. **用户手动改的内容，要脱离 source mapping 体系**
   - AI 生成的部分：有 source_ids，可悬停高亮
   - 用户手改的部分：没有来源，标记为 `user_edited`
   - 否则用户改完，悬停高亮就乱了

2. **手改后是否重新校验？**
   建议给提示而非强制：
   - 用户新增了原文没有的事实 → 温和提示“此内容无原文依据”
   - 但不阻止，最终责任在用户

**数据结构上加一个字段：**

```json
{
  "out_017": {
    "text": "...",
    "source_ids": ["W1_L3"],
    "origin": "ai" | "user_edited"
  }
}
```

**user_edited 的处理规则：**

| 场景 | 处理 |
|---|---|
| 用户手改某句 | origin = user_edited；删 data-src；mapping 保留 |
| 用户再次点【进一步编辑】 | 可继续改；改完还是 user_edited |
| 点【重新生成】 | **AI 生成的部分重新生成；user_edited 的部分保留**（不覆盖用户改动） |
| 导出 | 导出“最终态”（AI + user_edited 合并） |
| 悬停高亮 | AI 句可高亮；user_edited 句不高亮 |

**重新生成的边界：**
```
遍历 output_store：
- origin = ai → 丢弃，重新生成
- origin = user_edited → 保留，不覆盖
重新生成后，AI 条目和 user_edited 条目合并展示
```

---

### 5. 悬停高亮的实现逻辑

**前端交互：**

```
用户悬停 out_017
  → 查 Mapping Table 拿到 source_ids = [W1_L3, P1_L5]
  → 原文区为 data-source-id="W1_L3" 和 "P1_L5" 的元素加 .hl 类
  → 同时为 out_017 自身加 .hl 类
```

**反向同理。**

**性能优化：**
- 生成时一次性构建映射，前端启动时加载到内存
- 双向索引：`Map<outputId, sourceIds[]>` + `Map<sourceId, outputIds[]>`
- 悬停 O(1) 查询
- 高亮用 CSS class 切换，不直接操作 style

**CSS 选择器：**
```css
[data-src~="W1_L3"] { background: #FFF3B0; }
```

**注：**
1. **一对多、多对一都要支持**：一条输出可能来自多段原文，一段原文也可能被多条输出引用
2. **悬停时显示操作说明**：不只是高亮，还要告诉用户“这里做了压缩/改写”
3. **无来源的条目要报警**：如果某条输出 `source_ids` 为空，说明模型可能编造了内容 → 直接拦截或标记

第3点是**安全阀**：只要强制“每条输出必须有来源”，就能从机制上防止编造。

---

## 三、跟相关性引擎的衔接

Source Mapping 不负责“打分”，但生成流程要用到打分结果。

**生成前的流程：**

```
简历提炼（LLM）→ 结构化 experiences
JD提炼（LLM）→ 结构化 requirements
四维度打分（规则 + embedding）→ 每条经历的分数
质量调整 + 硬信号 + 分层（纯规则）→ 每条经历的层级
生成改写指令（LLM）→ changes 数组
本地校验（Gate 1-4）→ 最终输出 + mapping
```

**每次 LLM 调用都只输出结构化数据，不输出最终文本。**

| #   | 调用              | 方式             |
| --- | --------------- | -------------- |
| 1   | 简历提炼            | LLM            |
| 2   | JD提炼            | LLM            |
| 3   | 四维度打分           | 规则 + embedding |
| 4   | 质量调整 + 硬信号 + 分层 | 纯规则            |
| 5   | 改写指令生成          | LLM            |

详见 `相关性计算算法 (自研).md`。

---

## 四、核心设计原则

1. **Single Source of Truth** — 完整原始简历是唯一事实源
2. **Traceability** — 每条 AI 输出必须绑定 source_ids + operation
3. **Safety First** — Gate 1-4 四道门禁，不通过则回退
4. **LLM 只输出指令，不输出最终文本** — 从源头杜绝幻觉
5. **user_edited 不可覆盖** — 重新生成时保留用户手改内容
6. **规则优先** — 能规则化的（打分、分层、校验）不用 LLM

---

## 五、一句话总结

> **让 LLM 说“我想把 W1_L3 压缩成这样”，而不是“这是新简历”。**
> 前者可校验、可回退、可追溯；后者是黑箱。