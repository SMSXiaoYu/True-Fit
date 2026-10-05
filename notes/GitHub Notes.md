
ApplyRight-AI:[Rowe83/ApplyRight-AI](https://github.com/Rowe83/ApplyRight-AI)
Resume-Matcher:[srbhr/Resume-Matcher: The #1 AI Harness for Building Resumes, PDFs, Cover Letters & more, locally with 100+ LLMs support.](https://github.com/srbhr/Resume-Matcher)
resume-jd-optimizer-cn:[coinluu/resume-jd-optimizer-cn: JD-driven Chinese resume optimization skill for China job seekers. 解析岗位 JD、诊断简历缺口、生成真实可信的中文定制简历、ATS 纯文本版和面试追问。](https://github.com/coinluu/resume-jd-optimizer-cn)

防编造抄 Resume-Matcher，规则抄 resume-jd-optimizer-cn，解析抄 ApplyRight，**分层、source mapping、双向高亮、拖拽——这四样没人做好，就是 TrueFit 的护城河。**

## 一、直接可抄（拿来就用）

**1. 防编造三层机制 —— 抄 Resume-Matcher**  
这是三个项目里最值钱的资产，你的“不造假”底线直接靠它落地：

- Prompt 层：`CRITICAL_TRUTHFULNESS_RULES` 三档模板（nudge/keywords/full），9条铁律
- Diff 白名单：`_ALLOWED_PATH_PATTERNS`，禁止改 personalInfo/年份/公司/职位
- Bullet 级相似度回退：`_merge_description_rows` 用 `SequenceMatcher` 算相似度，低于阈值或检测到**新增数字**就强制回退原文
- Contract 保留：强制候选描述行数+样式与原文一致

**2. 贡献动词门槛 —— 抄 resume-jd-optimizer-cn**  
`支持/协助 < 参与 < 负责 < 推动 < 主导`，每档有事实门槛，不满足强制降级。直接搬，这是“不夸大”的核心规则。

**3. 三态事实标记 —— 抄 resume-jd-optimizer-cn**  
`已确认 / 待确认 / 模型推断`，只有“已确认”能进简历。这是你“只做减法”的理念抓手。  
**注意：** 你**不做追问**，所以“待确认”不是去问用户，而是**直接降权或剔除**。

**4. 事实底稿 Schema —— 抄 resume-jd-optimizer-cn**  
`经历编号 | 原文 | 已知事实 | 数据深度 | 成果等级 | 贡献动词上限 | 表达问题`  
这套字段直接作为 TrueFit 的经历对象 schema，尤其是“经历编号+原文”这个字段，就是你 source mapping 的原文锚点 ID。

**5. PDF 文本提取 —— 抄 ApplyRight**  
`extractPdfTextFromFile`：`pdfjs-dist` + 坐标按 y 优先、x 次序重排，解决 PDF 文本乱序。直接抄。

**6. 章节解析 —— 抄 ApplyRight**  
`parseResumeSections`：双语正则匹配标题切章节。但要**再加一层句子级 tokenize**，才能支撑句子级 mapping。

**7. Markdown 噪音清理 —— 抄 ApplyRight**  
`toPlainResumeText`：strip `**`/`#`/`-` 再 diff，避免干扰。直接抄。

**8. 匹配报告 UI —— 抄 Resume-Matcher**  
`ats-score-card.tsx`：总分+子分+缺失项+建议。`diff-preview-modal.tsx`：按 field_type 分组展开改动。样式直接参考。

## 二、需要改造（有雏形，不完整）

**1. 相关度打分 —— 三家都不够，要自研**

- ApplyRight：纯 LLM 直觉给分，不可靠
- Resume-Matcher：纯字面关键词，无语义
- resume-jd-optimizer-cn：7维度 rubric（硬性20/技能20/项目25/成果15/职级10/表达5/ATS5），最完整，但它是**整体匹配分**，不是相关性

**两个概念要分清：**

| | 7维度 rubric | 相关性维度 |
|---|---|---|
| 作用对象 | 整份简历 vs JD | 单条经历 vs JD |
| 输出 | 一个总分 | 每条经历的得分 |
| 用途 | 匹配报告（bonus） | 分层、排序、瘦身（核心） |

- **核心引擎**：自研4维度（技能/职责/行业/资历），底层用语义相似度（embedding），不用纯关键词
- **整体匹配分**：不单独做，由每条经历相关度分加权汇总而来，避免两套引擎
- **7维度 rubric**：降为参考清单，仅用于查漏；可抄两条规则：
  - 技能区无项目证据最多一半分
  - 高权重硬门槛无证据须单独提示高淘汰风险

相关性引擎是发动机，匹配报告是仪表盘。仪表盘数字来自发动机，不另装传感器。

**2. 分层逻辑 —— 三家都没有，必须自研**  
三个项目都**没有“核心区/相关经历/其他经历”三层概念**：

- Resume-Matcher 有 `injectable vs non_injectable` 二分雏形
- resume-jd-optimizer-cn 只做“按相关度排序前置”

**建议：** 把 Resume-Matcher 的二分思路扩展到三档，配合你的可调权重。

**3. Source mapping —— 三家都只到章节/bullet级，句子级要自研**

- ApplyRight：章节级关键词定位 + scrollIntoView
- Resume-Matcher：`_best_source_row` 用 SequenceMatcher 找最相似源行 —— **这是你最好的起点**
- resume-jd-optimizer-cn：只有文档级“修改说明”表，无交互

**建议：** 抄 Resume-Matcher 的 `_best_source_row` 思路，把输出映射到前端做双向 hover。

**4. 前端悬停双向高亮 —— 三家都没有，完全自研**

- ApplyRight：章节级 diff 双栏 + 词级红绿高亮（单向）
- Resume-Matcher：`segmentTextByKeywords` + `<mark>`（单向）
- 无人做 candidate↔source 双向 hover

**这是你最大的差异化，也是最难的部分。**

**5. 拖拽重排 UI —— 三家都没有**  
Resume-Matcher 的 tracker-reorder 是求职列表重排，不是简历内容重排。完全自研。

## 三、明确不抄（避免跑偏）

- resume-jd-optimizer-cn 的**追问流程**（你已明确不做）
- resume-jd-optimizer-cn 的**输入路由契约 A-E**（太重，你不需要）
- ApplyRight 的**纯 LLM 打分**（不可靠）
- Resume-Matcher 的 **tracker/cover-letter/print** 等外围模块


## Q&A

**Q1: “核心区/相关经历/其他经历”指什么？**

这是你**输出简历的分层结构**，不是原文的章节，而是按“与JD的相关度”重新分组后的呈现方式。
假设原始简历里有5段经历：A、B、C、D、E。JD 要求的是“高并发后端 + 带团队”。

|层级|放什么|呈现方式|例子|
|---|---|---|---|
|**核心区**|与JD高度相关的经历|详细展开、前置、措辞对齐JD|A经历（做过高并发）|
|**相关经历**|有一定相关性的|简要罗列，一条一句|C经历（带过小团队）|
|**其他经历**|与JD无关的|压缩成一行或仅保留标题|D经历（做过前端）|
|（可删）|完全不相关且占篇幅|直接去掉|E经历（大学社团）|

**关键点：**
- 这是**你重新定义的简历结构**，不是原文自带的
- 核心区在前，相关经历居中，其他经历在后
- 目的是**引导HR的视线**，让匹配点3秒内被看到

**Q2: 防编造，是因为LLM可能生成编造的内容，但是我们去掉了该内容吗？**

对，但**不只是“去掉”**，而是分三层处理：

**第一层：生成前约束（Prompt）**  
告诉LLM：“只允许压缩、重排、换措辞，禁止新增事实”。这是预防。
**第二层：生成后校验（代码）**  
LLM生成完，用代码检查：
- 每条输出有没有 `source_ids`（来源）
- 有没有新增原文没有的数字、公司、技能
- 有没有把“参与”改成“主导”

发现问题 → **强制回退原文**，不是简单删掉。比如：
- 原文：“参与订单系统重构”
- LLM改成：“主导订单系统重构，QPS提升300%”
- 校验发现：①“主导”超出贡献门槛 ②“300%”原文没有
- 处理：**回退成原文的“参与订单系统重构”**，或标记为违规

**第三层：白名单限制**  
禁止改某些字段（如公司名、时间、职位），从机制上锁死。

**所以答案是：** 不是“发现编造就删掉”，而是“**发现编造就回退到原文的真实版本**”，保证输出永远是原文的子集。

**Q3: 规则指的是什么？**
**A. 防编造规则（安全规则）**  
用来判断“这句话能不能这么改”，跟相关性无关。
- 贡献动词不能升级（参与≠主导）
- 不能新增数字、公司、技能、时间
- 不能删除已存在项（只能降权/压缩）
- 日期不能改
**B. 相关性规则（匹配规则）**  
用来判断“这段经历跟JD有多相关”，决定它进核心区还是其他经历。
- 技能关键词重合度
- 岗位职责相似度
- 行业/领域相关性
- 资历层级匹配度
