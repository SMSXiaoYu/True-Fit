

# 调用1：简历提炼Prompt

你是一个简历结构化提取器。请把下面的简历原文，提取成结构化 JSON。

## 一、提取规则

1. 按“业务主题”把简历切成独立的经历单元（E1、E2、E3...）
   - 同一业务主题跨章节的（比如工作经历和项目经历都提到“订单”），合并成一个 E
   - 去重、不重叠

2. 每个经历单元，提取以下字段：
   - id：编号（E1、E2...）
   - title：业务模块名
   - role：角色 + 负责范围
   - time：时间
   - tech：技术栈（数组）
   - actions：做了什么（数组）
   - results：结果（定性，数组）
   - metrics：量化指标（数组）
   - industry：行业
   - collaboration：协作对象（可选）
   - seniority：资历层级（可选）
   - source：原文来源（数组，用 W1_L3、P1_L5 这种编号）

3. **不要新增原文没有的内容**。只做提取和归类，不做改写。

## 二、字段规则（严格遵守）

### time
- 同一业务主题跨工作经历和项目经历时，统一格式：工作经历时间 / 项目经历时间
- 用 " / " 分隔，不保留括号注释
- 例："2021.07-至今 / 2022.03-2022.09"

### tech（技术栈）
- 只提取明确的技术名词：编程语言、框架、数据库、中间件、工具、协议/规范
- **要求 tech 字段尽量完整**，包含这段经历中实际用到的所有技术：
  - 原文明确列出的技术
  - 能从原文推断出的技术（如"用SSM框架写CRUD" → SSM、Java；"用POI做Excel导出" → POI）
  - 如果原文提到"支付回调"，相关的技术栈（如 Java、Spring）也应包含
- 禁止放入：
  - 动作（"对接接口"、"写解析逻辑"）
  - 对象（"对账单文件"、"订单支付记录"）
  - 业务名词（"支付回调"、"订单状态机"）
- 例：["Spring Boot", "MyBatis", "MySQL", "ES"]

### role
- 只含：角色 + 负责范围
- 角色：核心开发 / 独立负责 / 参与 / 协助
- 负责范围：具体模块名
- 组织/组别信息（如"交易与订单组"）不放 role
- 例："核心开发之一，负责订单状态流转模块"

### collaboration
- 只含直接协作对象：产品、测试、前端、DBA、运维、项目经理、校招生、mentor
- 不含间接受益方（财务、用户）

### actions
- 一条 action = 一个独立动作
- 不同动作不合并
- 例：["对接对账单接口", "写解析逻辑"]（不合并成一条）

### title
- 用原文中出现的表述，全篇统一
- 不创造原文没有的表述
- 同一业务主题的 title 应保持一致

### results / metrics
- results：定性结果
- metrics：量化指标
- 两者不重复计分

### source
- 每条经历，记录它的原文来源编号（W1_L3、P1_L5 等）
- 编号规则：W=工作经历，P=项目经历，L=行号

## 三、输出格式

```json
{
  "experiences": [
    {
      "id": "E1",
      "title": "...",
      "role": "...",
      "time": "...",
      "tech": ["..."],
      "actions": ["..."],
      "results": ["..."],
      "metrics": ["..."],
      "industry": "...",
      "collaboration": "...",
      "seniority": "...",
      "source": ["...", "..."]
    }
  ]
}
```

四、简历原文
（把简历A的纯文本贴在这里）

# 调用2：JD提炼Prompt

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
- **拆分规则：逗号分隔的并列动作，每个拆成一条**
  - 例："业务后端接口开发、系统优化、线上问题排查" → 拆成3条
- 只提取职责动作，不提取技能
- 例：[{"item": "AI Agent后端开发", "weight": 3}, {"item": "系统优化", "weight": 2}]

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
- 编号规则：D=Duty（岗位职责），R=Requirement（任职要求），B=Bonus（加分项）
- **无来源用空数组 `[]`，不用 null**
- 例：{"hard.education": ["R1"], "skills[0]": ["R2"], "industry.required": []}

### source_store（原文库）
- 存原文片段，按编号
- 例：{"R1": "本科及以上，1-3年后端开发经验", "R2": "熟练Java/Go其一，扎实计算机基础"}

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

# 调用5：改写指令生成

## 任务
根据 JD 核心要求、简历经历结构化数据、每条经历的分层结果，生成改写指令（changes 数组）。

## 角色
你是简历改写指令生成器。你输出的不是简历文本，而是"改写指令"——每条指令告诉后端：用什么操作、从哪几段原文、改成什么文本。

## 原则
1. 只做减法，不新增任何事实
2. 所有输出可追溯到原文
3. 无法改写时，宁可原文照搬

## 硬性规则（违反即回退）

1. 只允许压缩、重排、换措辞，禁止新增任何事实
2. text 中的所有数字必须来自 source_ids 对应的原文
3. 贡献动词不能升级：原文"参与"不能改成"主导"
4. 禁止新增原文没有的公司名、技能名、时间
5. 只允许使用 allowed_operations 中列出的操作类型
6. 每条输出必须包含 source_ids，为空则拒绝
7. 如果无法在规则内完成压缩，输出 operation: "verbatim" 和原文

## 输出 Schema（严格遵守）

你的输出必须是一个 JSON 对象，结构如下：

```json
{
  "changes": [
    {
      "output_id": "string, 格式 out_001、out_002... 递增",
      "operation": "string, 枚举值：verbatim | compress | reword | reorder | merge | demote | omit",
      "source_ids": "array of string, 不能为空，每一项必须是输入数据中存在的 source_id",
      "text": "string, 改写后的文本；operation 为 omit 时填 null",
      "reason": "string, 一句话说明为什么这样改"
    }
  ]
}
```

### 字段约束

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| output_id | string | 是 | 格式 `out_XXX`，从 out_001 递增 |
| operation | string | 是 | 只能是 7 种枚举值之一 |
| source_ids | array | 是 | 不能为空；每项必须是真实存在的 source_id |
| text | string | 是 | operation=omit 时为 null；其他情况必须是 source_ids 对应原文的压缩/重排/换措辞 |
| reason | string | 是 | 一句话，不超过 30 字 |

### operation 枚举说明

| operation | 含义 | text 要求 |
|---|---|---|
| verbatim | 原文照搬 | 与原文完全一致 |
| compress | 压缩冗余 | 保留全部事实，去掉冗余表达 |
| reword | 换措辞对齐JD | 用JD的措辞重写，事实不变 |
| reorder | 调整顺序 | 原文顺序调整，文字不变 |
| merge | 多段合并 | 多段原文合并为一条 |
| demote | 保留但降权 | 文本保留，layer 降级 |
| omit | 彻底不出现 | text 为 null |

## 输入数据

### JD核心要求

```json
{
  "jd_id": "JD1_backend",
  "title": "后端开发工程师（AI Agent）",
  "requirements": {
    "hard": {
      "education": "本科及以上",
      "experience": "1-3年后端经验",
      "preferred": ["大厂经历"]
    },
    "skills": [
      { "item": "Java/Go", "weight": 3 },
      { "item": "MySQL", "weight": 3 },
      { "item": "Redis", "weight": 2 },
      { "item": "分布式基础", "weight": 2 },
      { "item": "Linux", "weight": 2 },
      { "item": "Agent/大模型应用", "weight": 1, "type": "bonus" }
    ],
    "responsibilities": [
      { "item": "AI Agent后端开发", "weight": 3 },
      { "item": "业务接口开发", "weight": 3 },
      { "item": "系统优化", "weight": 2 },
      { "item": "线上问题排查", "weight": 2 },
      { "item": "技术方案设计", "weight": 2 }
    ],
    "industry": {
      "required": ["AI/大模型应用"],
      "preferred": ["教育行业"]
    },
    "level": {
      "years": "1-3年",
      "title": null
    }
  }
}
```

### 简历经历（带分层结果）

```json
{
  "experiences": [
    {
      "id": "E1",
      "title": "订单中心重构",
      "tier": "related",
      "allowed_operations": ["verbatim", "compress", "reorder"],
      "source_ids": ["W1_L3", "P1_L3", "P1_L4", "P1_L5", "P1_L6"],
      "original_texts": {
        "W1_L3": "参与订单中心的重构工作，把原来一个比较大的单体服务拆成了订单创建、订单查询、订单状态流转几个模块，我主要负责订单状态流转这一块，用Spring Boot + MyBatis + MySQL实现，重构之后订单状态相关的接口平均响应时间从原来的300ms左右降到了120ms左右。",
        "P1_L3": "我的职责：作为核心开发之一，负责订单状态流转模块的设计和开发，以及部分订单查询接口的优化。",
        "P1_L4": "技术方案：用Spring Boot搭建新服务，数据库做了分表，订单表按用户ID哈希分16张表，查询走ES做部分字段的检索。",
        "P1_L5": "主要工作：定义了订单状态机，把订单状态流转统一收口到一个服务里；写了状态变更的幂等处理；配合DBA做了数据迁移。",
        "P1_L6": "结果：改造后订单相关接口的P99从800ms降到了300ms左右，发布频率从两周一次变成了一周两次。"
      }
    },
    {
      "id": "E2",
      "title": "支付链路",
      "tier": "related",
      "allowed_operations": ["verbatim", "compress", "reorder"],
      "source_ids": ["W1_L4", "W1_L5", "P2_L3", "P2_L4", "P2_L5"],
      "original_texts": {
        "W1_L4": "负责支付回调链路的稳定性优化，之前经常出现支付成功了但是订单状态没更新，用户投诉比较多，后来加了回调幂等处理、失败重试、对账补偿任务，把支付回调相关的线上问题从每月大概15起降到了每月2-3起。",
        "W1_L5": "写了一个对账脚本，每天凌晨跑，把第三方支付平台的流水和我们自己的订单支付记录做比对，发现不一致的会发告警邮件，这个脚本后来交给了运维同学一起维护。",
        "P2_L3": "我的职责：独立负责对账系统的后端开发。",
        "P2_L4": "主要工作：对接了微信支付和支付宝的对账单文件下载接口，写了解析逻辑，和内部订单支付记录做比对，差异数据落库并生成报表。",
        "P2_L5": "结果：对账从原来人工大概半天的工作量缩短到系统自动跑15分钟左右，财务只需要处理差异数据。"
      }
    },
    {
      "id": "E3",
      "title": "会员与营销",
      "tier": "other",
      "allowed_operations": ["verbatim", "compress", "demote"],
      "source_ids": ["W2_L3", "W2_L4", "W2_L5", "W2_L6"],
      "original_texts": {
        "W2_L3": "负责会员积分系统的日常开发和维护，包括积分获取、积分消耗、积分过期等逻辑，用的是Spring Cloud + Redis + MySQL。",
        "W2_L4": "参与优惠券系统的开发，支持满减券、折扣券、兑换券几种类型，写了一些发券和核销的接口。",
        "W2_L5": "优化过积分过期的定时任务，原来是一次性把所有过期积分查出来处理，数据量大了之后经常超时，后来改成分页批量处理，任务执行时间从40多分钟降到了10分钟以内。",
        "W2_L6": "修过一些线上bug，比如并发领券导致超发的问题，后来加了Redis分布式锁。"
      }
    },
    {
      "id": "E4",
      "title": "企业信息化",
      "tier": "other",
      "allowed_operations": ["verbatim", "compress", "demote"],
      "source_ids": ["W3_L3", "W3_L4", "P3_L4", "P3_L5"],
      "original_texts": {
        "W3_L3": "在进销存项目里负责库存模块的开发，包括入库、出库、库存盘点等功能，用的是SSM框架。",
        "W3_L4": "在质检项目里负责质检单的录入和查询功能，还有一部分报表导出功能，用的是POI。",
        "P3_L4": "主要工作：用SSM框架写CRUD接口，用POI做Excel导出，写了几个统计SQL。",
        "P3_L5": "结果：客户那边质检记录实现了电子化，查询历史记录方便了很多。"
      }
    },
    {
      "id": "E5",
      "title": "市场实习",
      "tier": "other",
      "allowed_operations": ["verbatim", "omit"],
      "source_ids": ["W4_L3", "W4_L4", "W4_L5"],
      "original_texts": {
        "W4_L3": "负责在高校周边发放宣传单页，每天大概发200-300份。",
        "W4_L4": "协助组织过两场考研讲座，一场大概来了80多人，另一场来了50多人。",
        "W4_L5": "整理过意向学员的联系方式，录入Excel表格，大概录了500多条。"
      }
    }
  ]
}
```

## 任务（重述）

现在，请为上述 experiences 数组中的所有经历，生成改写指令（changes 数组）。

要求：
1. 每条经历都要处理（verbatim / compress / reword / demote / omit 等）
2. 严格按 allowed_operations 允许的操作执行
3. 所有数字、事实必须来自 original_texts
4. 贡献动词不能升级
5. 输出严格符合上述 Schema 的 JSON 对象，不要输出任何其他文字
