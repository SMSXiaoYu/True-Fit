你是一个JD结构化提取器。请把下面的JD原文，提取成结构化 JSON。

## 一、提取规则

1. 把 JD 提炼成 5 个维度：
   - 硬性条件（hard）
   - 核心技能（skills）
   - 核心职责（responsibilities）
   - 行业领域（industry）
   - 资历层级（level）

2. **不要新增原文没有的内容**。只做提取和归类，不做改写。
3. 如果某个维度原文没提，填 null 或空数组。

## 二、字段规则（严格遵守）

### hard（硬性条件）
- 固定 3 字段：
  - education：学历要求
  - experience：经验要求
  - preferred：加分条件（数组）
- 无内容填 null 或 []
- **preferred 只从 JD 的"加分项/优先条件"里提取，保留原文表述（含"优先"二字）**
- **不要把 skills 里的 bonus 项塞进 hard.preferred**

### skills（核心技能）
- 动态列表，每条：{ item, weight, type? }
- **weight 判断规则：**
  - 熟练 / 熟悉 → high（3）
  - 了解 → medium（2）
  - 优先 / 加分 → low（1），并加 type: "bonus"
- type 可选，默认普通项，bonus 表示加分项
- 只提取明确的技能名词（语言、框架、工具、能力）
- 例：[{"item": "Java/Go", "weight": 3}, {"item": "MySQL", "weight": 3}]

### responsibilities（核心职责）
- 动态列表，每条：{ item, weight }
- weight 取值：high=3 / medium=2 / low=1
- **拆分规则：按"职责方向"拆，不按逗号拆**
  - 默认不拆：一条 JD 职责描述，通常是一个完整的职责方向，不拆
  - 判断标准：拆开后，每条是否是一个"独立的职责方向"
  - 例："负责AIAgent后端服务开发，实现智能体调度、工具调用、记忆模块、对话链路等功能开发" → 这是"一件事"（AI Agent开发），不拆
  - 例："业务后端接口开发、系统优化、线上问题排查" → 这是"一件事"（后端业务开发），不拆
  - 例："负责后端开发、负责测试、负责运维" → 这是"三件事"，拆成3条
- 只提取职责动作，不提取技能
- 例：[{"item": "AI Agent后端服务开发", "weight": 3}, {"item": "业务后端接口开发", "weight": 3}, {"item": "系统优化", "weight": 2}]

### industry（行业领域）
- 固定 2 字段：
  - required：必须的行业（数组）
  - preferred：优先的行业（数组）
- 无内容填 null 或 []
- **不要推断**，JD没写行业就填 null

### level（资历层级）
- 固定 2 字段：
  - years：年限要求
  - title：职级要求
- 无内容填 null

### source_map（溯源）
- 记录每个提炼项对应的原文编号
- **编号规则：**
  - D = Duty（岗位职责）
  - R = Requirement（任职要求）
  - B = Bonus（加分项）
- **编号示例：**
  - 岗位职责第1条 → D1
  - 任职要求第1条 → R1
  - 加分项第1条 → B1
- **key 生成规则：数组字段统一用 `field[index]` 形式**
  - 例：`skills[0]`、`skills[1]`、`hard.preferred[0]`、`hard.preferred[1]`
  - **不允许用聚合形式**（如 `hard.preferred`）
  - **不允许有时用索引、有时用聚合**
- **无来源用空数组 `[]`，不用 null**
- **无来源时也要显式写 key**，如 `"industry.required": []`
- 例：{"hard.education": ["R1"], "skills[0]": ["R2"], "hard.preferred[0]": ["R1"], "industry.required": []}

### source_store（原文库）
- 存原文片段，按编号
- 例：{"D1": "负责AIAgent后端服务开发...", "R1": "本科及以上，1-3年后端开发经验", "B1": "有AI智能体、向量数据库..."}

## 三、输出格式

```json
{
  "jd_id": "...",
  "title": "...",
  "requirements": {
    "hard": {
      "education": "...",
      "experience": "...",
      "preferred": ["..."]
    },
    "skills": [
      { "item": "...", "weight": 3 },
      { "item": "...", "weight": 2, "type": "bonus" }
    ],
    "responsibilities": [
      { "item": "...", "weight": 3 }
    ],
    "industry": {
      "required": ["..."],
      "preferred": ["..."]
    },
    "level": {
      "years": "...",
      "title": null
    }
  },
  "source_store": {
    "D1": "...",
    "R1": "...",
    "B1": "..."
  },
  "source_map": {
    "hard.education": ["R1"],
    "skills[0]": ["R2"]
  }
}
```
## 四、JD原文

（把 JD的原文贴在这里）