# -*- coding: utf-8 -*-
"""调用6：匹配报告生成（纯规则 + 模板，不调用 LLM）。

逻辑提炼自 tests/report_generator.py（不改规则，只做产品化封装、去掉文件 IO）。

报告口径:
  - 简历级四维度分数 = 该维度在所有经历上的最高分
  - 总体匹配分 overall_score = 简历级四维度加权后 ×10（100 分制）
  - 命中/缺口判定复用 step3_score 的 SKILL_SYNONYMS / RESP_KEYWORDS

产品函数: generate_report(scores, changes, jd, resume) -> str（返回 Markdown）
  scores = step3+step4 产出的 dict（rows 内含 scores/detail/tier，可携带 groups）
"""
import re

try:
    from .step3_score import SKILL_SYNONYMS, RESP_KEYWORDS, HARD_SIGNALS, weighted
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from step3_score import SKILL_SYNONYMS, RESP_KEYWORDS, HARD_SIGNALS, weighted

# operation 元数据（图标 / 中文标签 / 固定操作定义）
OP_META = {
    "verbatim": ("✅", "原文保留", "原文保留，未做修改"),
    "compress": ("✂️", "精简压缩", "删除低相关细节，保留核心信息"),
    "reword":   ("🔄", "换措辞",   "换措辞对齐JD，事实不变"),
    "reorder":  ("🔼", "前置",     "调整段落顺序，前置高相关经历"),
    "merge":    ("🔗", "合并",     "多条动作合并为一句，保留全部量化指标"),
    "demote":   ("📉", "降低权重", "从核心降级为相关/其他内容"),
    "omit":     ("⏭️", "省略",    "与岗位完全无关，整体省略不展示"),
}

# 缺口原因词典（从 JD 要求本身推断）
GAP_REASON_SKILL = {
    "计算机基础": "tech/actions 与技能区均未出现数据结构、操作系统、计算机网络等基础关键词",
    "独立开发、故障排查能力": "tech/actions 未体现独立完成项目或线上故障定位的完整描述",
    "Agent/大模型应用开发经验": "无智能体调度、工具调用、记忆、对话链路相关经历",
    "AI智能体": "无 AI 智能体相关项目或技术栈",
    "向量数据库": "无向量数据库（Milvus/FAISS 等）使用经历",
    "LLM服务对接项目经验": "无 LLM API 对接、Prompt 工程相关项目",
}
GAP_REASON_RESP = {
    "负责AIAgent后端服务开发": "简历以电商交易类后端为主，无 Agent 后端服务开发经历",
    "实现智能体调度、工具调用、记忆模块、对话链路等功能开发": "无调度、工具调用、记忆模块、对话链路相关功能开发经历",
    "跟进大模型相关技术落地": "无大模型技术调研或落地实践",
}
GAP_REASON_GENERIC_SKILL = "简历 tech/actions 未命中该技能关键词（{kws}）"
GAP_REASON_GENERIC_RESP = "actions/role 未命中该职责关键词（{kws}）"

# 补充建议“具体内容”词典
ADVICE_DETAIL = {
    "计算机基础": "在项目描述中体现数据结构、操作系统、网络协议等基础功底",
    "独立开发、故障排查能力": "突出独立完成的小项目与线上问题定位案例（如对账脚本、告警排查）",
}

# 大厂关键词
BIG_TECH_KW = ["阿里", "腾讯", "字节", "美团", "百度", "京东", "华为", "网易",
               "拼多多", "滴滴", "快手", "蚂蚁", "小米", "Shopee", "TikTok"]

DIM_LABEL = {"skill": "技能", "content": "内容", "industry": "行业", "seniority": "资历"}
_DIM_ORDER = ["skill", "content", "industry", "seniority"]

# embedding 内容分：职责↔经历最高余弦相似度 ≥ 此阈值才算「命中」，否则计入缺口
# （bge-m3 dense 向量对同域中文天然偏高，0.60 由 A_backend_5y×JD1 实测分布标定：
#  真相关职责 0.70+，AI Agent 等无关职责 0.55~0.62 以下沿）
CONTENT_SIM_THRESHOLD = 0.60
_ACTION_SNIPPET_LEN = 30  # best_action 摘要截断字数


def _distinct(seq):
    return list(dict.fromkeys(seq))


def _action_snippet(text, n=_ACTION_SNIPPET_LEN):
    """best_action 截断：去空白后取前 n 字，超出加省略号。"""
    text = re.sub(r"\s+", "", text or "")
    return text[:n] + ("…" if len(text) > n else "")


def _content_is_embedding(rows):
    """内容分是否走 embedding：看任一经历的 content 明细是否带 algo=embedding。"""
    return any((r.get("detail", {}).get("content") or {}).get("algo") == "embedding"
               for r in rows)


def _content_sim_matches(rows, item):
    """embedding 口径：返回某职责在各经历上的 [(max_sim, row, best_action)...]，降序。"""
    out = []
    for r in rows:
        d = (r.get("detail", {}).get("content", {}).get("hits") or {}).get(item)
        if isinstance(d, dict) and "max_sim" in d:
            out.append((float(d["max_sim"]), r, d.get("best_action", "")))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


# ============ 简历级汇总 ============

def resume_level_scores(rows, weights):
    """简历级四维度 = 各经历最高分；总体匹配分 = 加权 ×10。"""
    resume_scores = {k: max(r["scores"][k] for r in rows) for k in weights}
    overall = int(round(weighted(resume_scores, weights) * 10))
    return resume_scores, overall


# ============ 第2块：一句话结论 ============

def build_conclusion(resume_scores):
    """优势维度 ≥7；短板维度 <5；三种情况 + 均衡边界。"""
    strong = [DIM_LABEL[k] for k in _DIM_ORDER if resume_scores[k] >= 7]
    weak = [DIM_LABEL[k] for k in _DIM_ORDER if resume_scores[k] < 5]
    if strong and weak:
        return (f"{'与'.join(strong)}命中度高；{'与'.join(weak)}存在明显缺口，"
                f"建议按补充建议微调后投递。")
    if strong:
        return f"{'与'.join(strong)}命中度高，整体匹配良好，建议直接投递。"
    if weak:
        return f"{'与'.join(weak)}存在明显缺口，建议按补充建议补强后再投递。"
    return "各维度表现均衡，整体匹配良好，建议直接投递。"


# ============ 第4块：命中项 ============

def build_hit_items(jd, rows):
    lines = []
    jd_skills = jd["requirements"]["skills"]
    jd_resps = jd["requirements"]["responsibilities"]

    for s in jd_skills:
        item = s["item"]
        bonus = s.get("type") == "bonus"
        ev, kws = [], []
        for r in rows:
            h = r["detail"]["skill"]["hits"].get(item)
            if not h:
                continue
            src, ks = h
            kws += ks
            if src == "tech":
                ev.append(f"{r['id']} {'/'.join(ks)}")
            elif src == "actions":
                ev.append(f"{r['id']} actions:{'/'.join(ks)}")
            else:
                ev.append(f"{r['id']} 技能区声明")
        if item == "教育行业从业经验":
            for r in rows:
                if "教育" in r["industry"]:
                    ev.append(f"{r['id']} {r['title']}（{r['industry']}）")
                    kws.append("教育行业")
        if ev:
            tag = "（bonus）" if bonus else ""
            lines.append(f"{item}{tag}：命中 {'、'.join(_distinct(kws))} — {'、'.join(ev)}")

    # ---- 职责命中：keyword / embedding 两种口径 ----
    embedding = _content_is_embedding(rows)
    for resp in jd_resps:
        item = resp["item"]
        if embedding:
            # embedding：阈值以上的经历才算命中，展示最高相似度 + 最匹配 action
            matches = [(v, r, a) for v, r, a in _content_sim_matches(rows, item)
                       if v >= CONTENT_SIM_THRESHOLD]
            if not matches:
                continue
            top = matches[:3]
            ev = "、".join(f"{r['id']} {v:.2f}「{_action_snippet(a)}」" for v, r, a in top)
            lines.append(f"{item}：最高相似度 {top[0][0]:.2f}（{ev}）")
            continue

        ev, kws = [], []
        for r in rows:
            ks = r["detail"]["content"]["hits"].get(item)
            if not ks:
                continue
            kws += ks
            ev.append(f"{r['id']} {r['title']}")
        if ev:
            lines.append(f"{item}：命中 {'、'.join(_distinct(kws))} — {'、'.join(ev)}")
    return lines


# ============ 第5块：缺口项 ============

def build_gap_items(resume, jd, rows):
    """返回 (展示行, 结构化缺口列表)。"""
    lines, gaps = [], []
    jd_skills = jd["requirements"]["skills"]
    jd_resps = jd["requirements"]["responsibilities"]

    for s in jd_skills:
        item = s["item"]
        bonus = s.get("type") == "bonus"
        hit = any(r["detail"]["skill"]["hits"].get(item) for r in rows)
        if item == "教育行业从业经验":
            hit = hit or any("教育" in r["industry"] for r in rows)
        if hit:
            continue
        reason = GAP_REASON_SKILL.get(
            item,
            GAP_REASON_GENERIC_SKILL.format(
                kws="、".join(SKILL_SYNONYMS.get(item, [item.lower()]))))
        tag = "（bonus）" if bonus else ""
        lines.append(f"{item}{tag}：{reason}")
        gaps.append({"kind": "skill", "item": item, "bonus": bonus, "reason": reason})

    embedding = _content_is_embedding(rows)
    for resp in jd_resps:
        item = resp["item"]
        if embedding:
            # embedding：所有经历最高相似度都低于阈值才算缺口
            sims = _content_sim_matches(rows, item)
            top_sim = sims[0][0] if sims else 0.0
            if top_sim >= CONTENT_SIM_THRESHOLD:
                continue
            hard_tag = "（硬信号）" if item in HARD_SIGNALS else ""
            # 优先用人工精准原因，否则给出 embedding 口径的量化原因
            reason = GAP_REASON_RESP.get(item) or (
                f"所有经历与该职责最高语义相似度仅 {top_sim:.2f}"
                f"（命中阈值 {CONTENT_SIM_THRESHOLD:.2f}）")
            lines.append(f"{item}{hard_tag}：{reason}")
            gaps.append({"kind": "resp", "item": item, "bonus": False, "reason": reason})
            continue

        if any(r["detail"]["content"]["hits"].get(item) for r in rows):
            continue
        hard_tag = "（硬信号）" if item in HARD_SIGNALS else ""
        reason = GAP_REASON_RESP.get(
            item,
            GAP_REASON_GENERIC_RESP.format(
                kws="、".join(RESP_KEYWORDS.get(item, [item]))))
        lines.append(f"{item}{hard_tag}：{reason}")
        gaps.append({"kind": "resp", "item": item, "bonus": False, "reason": reason})

    jd_years = jd["requirements"]["level"]["years"] or ""
    m = re.search(r"(\d+)\s*-\s*(\d+)", jd_years)
    work_years = (resume.get("basic_info") or {}).get("work_years") or 0
    if m and work_years > int(m.group(2)):
        reason = f"实际 {work_years} 年，存在资历偏高风险"
        lines.append(f"资历 {jd_years}：{reason}")
        gaps.append({"kind": "seniority", "item": "资历", "bonus": False, "reason": reason})

    preferred = jd["requirements"]["hard"].get("preferred") or []
    all_text = " ".join((resume.get("source_store") or {}).values())
    if any("大厂" in p for p in preferred) and not any(k in all_text for k in BIG_TECH_KW):
        reason = "简历公司均为中小型企业，无大厂背景（优先项，非硬门槛）"
        lines.append(f"大厂经历（优先项）：{reason}")
        gaps.append({"kind": "bigtech", "item": "大厂经历", "bonus": False, "reason": reason})

    return lines, gaps


# ============ 第6块：改动说明 ============

def build_change_lines(changes, rows, groups):
    """按 operation 分组输出改动说明。groups: {group_id: [member_exp_id...]}。"""
    lines = []
    by_op = {}
    for c in changes:
        by_op.setdefault(c["operation"], []).append(c)
    group_changes = {}
    for c in changes:
        group_changes.setdefault(c.get("group_id"), []).append(c)

    def exp_ids_of(c):
        gids = groups.get(c.get("group_id"), [])
        cs = group_changes[c.get("group_id")]
        if len(gids) > 1 and len(cs) == len(gids):
            return [gids[cs.index(c)]]
        return gids

    for op, cs in by_op.items():
        icon, label, definition = OP_META[op]
        lines.append(f"{icon} {label}：{definition}")
        for c in cs:
            exp_ids = exp_ids_of(c)
            titles = [r["title"] for r in rows if r["id"] in exp_ids]
            names = "、".join(titles) if titles else "、".join(c["source_ids"])
            note = c["reason"]
            if op == "omit" and c.get("text") is None:
                note += "（text=null）"
            lines.append(f"{names}：{note}")
    return lines


# ============ 第7块：补充建议 ============

def _short_skill(item):
    return re.sub(r"(项目经验|开发经验|经验)$", "", item)


def build_advices(gaps, resume, jd, rows):
    advices = []

    must = [g for g in gaps if g["kind"] == "skill" and not g["bonus"]]
    if must:
        skills = "、".join(g["item"] for g in must)
        detail = "；".join(
            ADVICE_DETAIL.get(g["item"], f"在经历中补充与「{g['item']}」直接相关的实践")
            for g in must)
        advices.append(f"补充{skills}经历：{detail}，哪怕是个人项目也建议列出。")

    bonus_gaps = [g for g in gaps if g["kind"] == "skill" and g["bonus"]]
    if bonus_gaps:
        skills = "、".join(_short_skill(g["item"]) for g in bonus_gaps)
        advices.append(f"若有{skills}经验，建议在技能区显式声明并补一条经历证据。")

    preferred = jd["requirements"]["industry"].get("preferred") or []
    for p in preferred:
        core = p.replace("行业", "")
        hit = next((r for r in rows if core and core in r["industry"]), None)
        if hit:
            advices.append(f"{hit['title']}虽与技术无关，但{p}是JD偏好行业，"
                           f"可用一行带过，不必整段删除。")

    jd_years = jd["requirements"]["level"]["years"] or ""
    m = re.search(r"(\d+)\s*-\s*(\d+)", jd_years)
    work_years = (resume.get("basic_info") or {}).get("work_years") or 0
    if m and work_years > int(m.group(2)):
        advices.append(f"资历高于JD要求（{work_years}年 vs {jd_years}），"
                       f"建议弱化年限表述、突出技术深度与独立产出，避免被判定 overqualified。")

    if any(g["kind"] == "bigtech" for g in gaps):
        advices.append("大厂经历为优先项，非硬门槛；建议突出项目规模、技术深度来弥补。")

    return advices[:5]


# ============ 产品入口 ============

def generate_report(scored, changes, jd, resume):
    """生成完整匹配报告 Markdown。

    参数:
        scored: step3+step4 的产出（rows 含 scores/detail/tier；可含 groups/weights）
        changes: Gate 校验通过的 changes 列表
        jd:     JD 完整 dict
        resume: 简历完整 dict
    返回:
        Markdown 字符串
    """
    rows = scored["rows"]
    weights = scored.get("weights")
    groups = scored.get("groups", {})

    resume_scores, overall = resume_level_scores(rows, weights)
    conclusion = build_conclusion(resume_scores)
    hits = build_hit_items(jd, rows)
    gap_lines, gaps = build_gap_items(resume, jd, rows)
    change_lines = build_change_lines(changes, rows, groups)
    advices = build_advices(gaps, resume, jd, rows)

    out = []
    a = out.append
    a("总体匹配")
    a(f"{overall} / 100")
    a(conclusion)
    a("")
    for key in _DIM_ORDER:
        a(DIM_LABEL[key])
        a(f"{resume_scores[key]:g} / 10")
        a("")
    a("✅")
    a("命中项（JD 要求 → 简历证据）")
    out.extend(hits)
    a("")
    a("⚠️")
    a("缺口项（JD 要求但简历未体现）")
    out.extend(gap_lines)
    a("")
    a("改动说明")
    out.extend(change_lines)
    a("")
    a("补充建议")
    for i, adv in enumerate(advices, 1):
        a(f"{i}. {adv}")

    return "\n".join(out)
