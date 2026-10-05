# -*- coding: utf-8 -*-
"""
匹配报告生成器（纯规则 + 模板，不调用任何 LLM）
输入:
  1. score_test.py 的打分结果 —— 直接 import score_test 复用其打分函数与词典，
     用 A_backend_5y + JD1_backend 现场重算（与 score_result.md 同口径）
  2. 调用5 的 changes 数组 —— 本脚本内置一份符合调用5输出 Schema 的示例数据
     （实际运行时由调用5产出传入，字段: operation / source_ids / text / reason / group_id）
输出: 控制台打印 + data/output/report_A.md

报告口径说明（规则定义）:
  - 简历级四维度分数 = 该维度在所有经历上的最高分（代表简历在该维度的最好水平）
  - 总体匹配分 overall_score = 简历级四维度按 权重(技能20%/内容45%/行业15%/资历10%)
    加权后 ×10 折算为 100 分制
  - 命中/缺口判定完全复用 score_test.py 的 SKILL_SYNONYMS / RESP_KEYWORDS 匹配结果
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT_PATH = BASE / "data" / "output" / "report_A.md"
CHANGES_PATH = BASE / "data" / "output" / "changes_A.json"

# 复用 score_test.py 的打分逻辑与数据路径（不改动 data/extracted）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_test as st

# ============ 从 changes_A.json 读取调用5的 changes 数组 ============
# group_id 对应环节U的聚类分组：G1~G6 为本测试数据的系统自动聚类结果
GROUPS = {
    "G1": ["E1"],           # 订单中心
    "G2": ["E2"],           # 支付链路
    "G3": ["E3"],           # 大促保障
    "G4": ["E4", "E5"],     # 会员积分 + 优惠券（营销域一组）
    "G5": ["E6", "E7"],     # 进销存 + 质检（企业信息化一组）
    "G6": ["E8"],           # 市场推广
}


def load_changes():
    """读取 changes_A.json，返回 changes 列表。"""
    data = json.loads(CHANGES_PATH.read_text(encoding="utf-8"))
    return data["changes"]

# ============ operation 元数据（图标 / 中文标签 / 固定操作定义） ============
OP_META = {
    "verbatim": ("✅", "原文保留", "原文保留，未做修改"),
    "compress": ("✂️", "精简压缩", "删除低相关细节，保留核心信息"),
    "reword":   ("🔄", "换措辞",   "换措辞对齐JD，事实不变"),
    "reorder":  ("🔼", "前置",     "调整段落顺序，前置高相关经历"),
    "merge":    ("🔗", "合并",     "多条动作合并为一句，保留全部量化指标"),
    "demote":   ("📉", "降低权重", "从核心降级为相关/其他内容"),
    "omit":     ("⏭️", "省略",    "与岗位完全无关，整体省略不展示"),
}

# ============ 缺口原因词典（规则：从 JD 要求本身推断） ============
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

# ============ 补充建议的"具体内容"词典（规则模板用） ============
ADVICE_DETAIL = {
    "计算机基础": "在项目描述中体现数据结构、操作系统、网络协议等基础功底",
    "独立开发、故障排查能力": "突出独立完成的小项目与线上问题定位案例（如对账脚本、告警排查）",
}

# 大厂关键词（用于判断简历是否有大厂经历）
BIG_TECH_KW = ["阿里", "腾讯", "字节", "美团", "百度", "京东", "华为", "网易",
               "拼多多", "滴滴", "快手", "蚂蚁", "小米", "Shopee", "TikTok"]


# ============ 打分重算（复用 score_test.py；内容分可选 embedding） ============
def _load_embedding_scorer():
    """内容分若已升级为 embedding（src/pipeline/step3_score.USE_EMBEDDING=True），
    返回该产品模块复用；否则返回 None，回退 score_test 的关键词算法。"""
    try:
        sys.path.insert(0, str(BASE))
        from src.pipeline import step3_score as prod
        return prod if getattr(prod, "USE_EMBEDDING", False) else None
    except Exception:
        return None


def compute_scores():
    resume, jd = st.load_data()
    jd_skills = jd["requirements"]["skills"]
    jd_resps = jd["requirements"]["responsibilities"]
    declared_hard = resume.get("skills_declared", {}).get("hard", [])

    # embedding 内容分（可选）：JD 职责向量只编码一次
    prod = _load_embedding_scorer()
    jd_emb = prod.encode_texts([r["item"] for r in jd_resps]) if prod else None

    rows = []
    for exp in resume["experiences"]:
        s_skill, d_skill = st.score_skill(jd_skills, exp, declared_hard)
        if prod:
            s_content, d_content = prod.score_content(jd_resps, exp, jd_emb=jd_emb)
        else:
            s_content, d_content = st.score_content(jd_resps, exp)
        s_ind, d_ind = st.score_industry(jd, exp)
        s_sen, d_sen = st.score_seniority(jd, exp)
        scores = {"skill": s_skill, "content": s_content,
                  "industry": s_ind, "seniority": s_sen}
        tier = st.calc_tier(scores, exp)
        rows.append({
            "id": exp["id"], "title": exp["title"],
            "industry": exp.get("industry") or "",
            "scores": scores, "tier": tier,
            "detail": {"skill": d_skill, "content": d_content},
        })

    # 简历级四维度 = 各经历最高分
    resume_scores = {k: max(r["scores"][k] for r in rows) for k in st.WEIGHTS}
    # 总体匹配分 = 简历级四维度加权 ×10 折算 100 分制
    overall = int(round(st.weighted(resume_scores) * 10))
    return resume, jd, rows, resume_scores, overall


def _distinct(seq):
    return list(dict.fromkeys(seq))


# ---- embedding 内容分的命中/缺口渲染辅助（与 src/pipeline/step6_report.py 同口径） ----
CONTENT_SIM_THRESHOLD = 0.60   # 职责↔经历最高余弦相似度 ≥ 此值才算命中
_ACTION_SNIPPET_LEN = 30       # best_action 摘要截断字数


def _action_snippet(text, n=_ACTION_SNIPPET_LEN):
    text = re.sub(r"\s+", "", text or "")
    return text[:n] + ("…" if len(text) > n else "")


def _content_is_embedding(rows):
    return any((r.get("detail", {}).get("content") or {}).get("algo") == "embedding"
               for r in rows)


def _content_sim_matches(rows, item):
    """返回某职责在各经历上的 [(max_sim, row, best_action)...]，按相似度降序。"""
    out = []
    for r in rows:
        d = (r.get("detail", {}).get("content", {}).get("hits") or {}).get(item)
        if isinstance(d, dict) and "max_sim" in d:
            out.append((float(d["max_sim"]), r, d.get("best_action", "")))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


# ============ 第4块：命中项 ============
def build_hit_items(resume, jd, rows):
    lines = []
    jd_skills = jd["requirements"]["skills"]
    jd_resps = jd["requirements"]["responsibilities"]

    # -- 技能命中 --
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
        # 规则补充：教育行业从业经验 → 用经历 industry 字段判定
        if item == "教育行业从业经验":
            for r in rows:
                if "教育" in r["industry"]:
                    ev.append(f"{r['id']} {r['title']}（{r['industry']}）")
                    kws.append("教育行业")
        if ev:
            tag = "（bonus）" if bonus else ""
            lines.append(f"{item}{tag}：命中 {'、'.join(_distinct(kws))} — {'、'.join(ev)}")

    # -- 职责命中：keyword / embedding 两种口径 --
    embedding = _content_is_embedding(rows)
    for resp in jd_resps:
        item = resp["item"]
        if embedding:
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
    """返回 (展示行, 结构化缺口列表)。结构化项含 kind/item/bonus/reason，供补充建议使用。"""
    lines, gaps = [], []
    jd_skills = jd["requirements"]["skills"]
    jd_resps = jd["requirements"]["responsibilities"]

    # -- 技能缺口 --
    for s in jd_skills:
        item = s["item"]
        bonus = s.get("type") == "bonus"
        hit = any(r["detail"]["skill"]["hits"].get(item) for r in rows)
        if item == "教育行业从业经验":  # 与命中项同规则：industry 字段判定
            hit = hit or any("教育" in r["industry"] for r in rows)
        if hit:
            continue
        reason = GAP_REASON_SKILL.get(
            item,
            GAP_REASON_GENERIC_SKILL.format(
                kws="、".join(st.SKILL_SYNONYMS.get(item, [item.lower()]))))
        tag = "（bonus）" if bonus else ""
        lines.append(f"{item}{tag}：{reason}")
        gaps.append({"kind": "skill", "item": item, "bonus": bonus, "reason": reason})

    # -- 职责缺口 --
    embedding = _content_is_embedding(rows)
    for resp in jd_resps:
        item = resp["item"]
        if embedding:
            sims = _content_sim_matches(rows, item)
            top_sim = sims[0][0] if sims else 0.0
            if top_sim >= CONTENT_SIM_THRESHOLD:
                continue
            hard_tag = "（硬信号）" if item in st.HARD_SIGNALS else ""
            reason = GAP_REASON_RESP.get(item) or (
                f"所有经历与该职责最高语义相似度仅 {top_sim:.2f}"
                f"（命中阈值 {CONTENT_SIM_THRESHOLD:.2f}）")
            lines.append(f"{item}{hard_tag}：{reason}")
            gaps.append({"kind": "resp", "item": item, "bonus": False, "reason": reason})
            continue

        if any(r["detail"]["content"]["hits"].get(item) for r in rows):
            continue
        hard_tag = "（硬信号）" if item in st.HARD_SIGNALS else ""
        reason = GAP_REASON_RESP.get(
            item,
            GAP_REASON_GENERIC_RESP.format(
                kws="、".join(st.RESP_KEYWORDS.get(item, [item]))))
        lines.append(f"{item}{hard_tag}：{reason}")
        gaps.append({"kind": "resp", "item": item, "bonus": False, "reason": reason})

    # -- 简历特征推断的缺口：资历偏高 --
    jd_years = jd["requirements"]["level"]["years"] or ""
    m = re.search(r"(\d+)\s*-\s*(\d+)", jd_years)
    work_years = (resume.get("basic_info") or {}).get("work_years") or 0
    if m and work_years > int(m.group(2)):
        reason = f"实际 {work_years} 年，存在资历偏高风险"
        lines.append(f"资历 {jd_years}：{reason}")
        gaps.append({"kind": "seniority", "item": "资历", "bonus": False, "reason": reason})

    # -- 简历特征推断的缺口：大厂经历（优先项） --
    preferred = jd["requirements"]["hard"].get("preferred") or []
    all_text = " ".join((resume.get("source_store") or {}).values())
    if any("大厂" in p for p in preferred) and not any(k in all_text for k in BIG_TECH_KW):
        reason = "简历公司均为中小型企业，无大厂背景（优先项，非硬门槛）"
        lines.append(f"大厂经历（优先项）：{reason}")
        gaps.append({"kind": "bigtech", "item": "大厂经历", "bonus": False, "reason": reason})

    return lines, gaps


# ============ 第6块：改动说明 ============
def build_change_lines(changes):
    lines = []
    by_op = {}  # 按 operation 分组（保持首次出现顺序）
    for c in changes:
        by_op.setdefault(c["operation"], []).append(c)
    # 组内 change ↔ 经历 配对：组内经历数与 change 数一致时按顺序一一对应，
    # 否则该 change 归属整组成员（如一条 merge 覆盖组内多段经历）
    group_changes = {}
    for c in changes:
        group_changes.setdefault(c.get("group_id"), []).append(c)

    def exp_ids_of(c):
        gids = GROUPS.get(c.get("group_id"), [])
        cs = group_changes[c.get("group_id")]
        if len(gids) > 1 and len(cs) == len(gids):
            return [gids[cs.index(c)]]
        return gids

    for op, cs in by_op.items():
        icon, label, definition = OP_META[op]
        lines.append(f"{icon} {label}：{definition}")
        for c in cs:
            exp_ids = exp_ids_of(c)
            titles = [r["title"] for r in _ALL_ROWS if r["id"] in exp_ids]
            names = "、".join(titles) if titles else "、".join(c["source_ids"])
            note = c["reason"]
            if op == "omit" and c.get("text") is None:
                note += "（text=null）"
            lines.append(f"{names}：{note}")
    return lines


# ============ 第7块：补充建议 ============
def _short_skill(item):
    """bonus 技能名归一化：去掉尾部『项目经验/开发经验/经验』，避免模板里出现重复『经验』。"""
    return re.sub(r"(项目经验|开发经验|经验)$", "", item)


def build_advices(gaps, resume, jd, rows):
    advices = []

    # 优先级1：必备技能缺口（非 bonus）
    must = [g for g in gaps if g["kind"] == "skill" and not g["bonus"]]
    if must:
        skills = "、".join(g["item"] for g in must)
        detail = "；".join(
            ADVICE_DETAIL.get(g["item"], f"在经历中补充与「{g['item']}」直接相关的实践")
            for g in must)
        advices.append(f"补充{skills}经历：{detail}，哪怕是个人项目也建议列出。")

    # 优先级2：bonus 项缺口
    bonus_gaps = [g for g in gaps if g["kind"] == "skill" and g["bonus"]]
    if bonus_gaps:
        skills = "、".join(_short_skill(g["item"]) for g in bonus_gaps)
        advices.append(f"若有{skills}经验，建议在技能区显式声明并补一条经历证据。")

    # 优先级3：特殊项 —— JD 偏好行业
    preferred = jd["requirements"]["industry"].get("preferred") or []
    for p in preferred:
        core = p.replace("行业", "")
        hit = next((r for r in rows if core and core in r["industry"]), None)
        if hit:
            advices.append(f"{hit['title']}虽与技术无关，但{p}是JD偏好行业，"
                           f"可用一行带过，不必整段删除。")

    # 优先级3：特殊项 —— 资历超配
    jd_years = jd["requirements"]["level"]["years"] or ""
    m = re.search(r"(\d+)\s*-\s*(\d+)", jd_years)
    work_years = (resume.get("basic_info") or {}).get("work_years") or 0
    if m and work_years > int(m.group(2)):
        advices.append(f"资历高于JD要求（{work_years}年 vs {jd_years}），"
                       f"建议弱化年限表述、突出技术深度与独立产出，避免被判定 overqualified。")

    # 优先级3：特殊项 —— 大厂经历
    if any(g["kind"] == "bigtech" for g in gaps):
        advices.append("大厂经历为优先项，非硬门槛；建议突出项目规模、技术深度来弥补。")

    return advices[:5]  # 3-5 条，按优先级截断


# ============ 第2块：一句话结论（三种情况 + 边界） ============
DIM_LABEL = {"skill": "技能", "content": "内容", "industry": "行业", "seniority": "资历"}
_DIM_ORDER = ["skill", "content", "industry", "seniority"]


def build_conclusion(resume_scores):
    """优势维度 = 分 ≥7；短板维度 = 分 <5；用"与"连接。"""
    strong = [DIM_LABEL[k] for k in _DIM_ORDER if resume_scores[k] >= 7]
    weak = [DIM_LABEL[k] for k in _DIM_ORDER if resume_scores[k] < 5]
    if strong and weak:  # 情况2：有优势，有短板
        return (f"{'与'.join(strong)}命中度高；{'与'.join(weak)}存在明显缺口，"
                f"建议按补充建议微调后投递。")
    if strong:  # 情况1：有优势，无短板
        return f"{'与'.join(strong)}命中度高，整体匹配良好，建议直接投递。"
    if weak:  # 情况3：无优势，有短板
        return f"{'与'.join(weak)}存在明显缺口，建议按补充建议补强后再投递。"
    # 边界：无优势也无短板（所有维度都在 5-7 之间）
    return "各维度表现均衡，整体匹配良好，建议直接投递。"


# ============ 主流程 ============
_ALL_ROWS = []  # build_change_lines 需要按 id 查经历名，主流程里填充


def run():
    global _ALL_ROWS
    resume, jd, rows, resume_scores, overall = compute_scores()
    _ALL_ROWS = rows

    hits = build_hit_items(resume, jd, rows)
    gap_lines, gaps = build_gap_items(resume, jd, rows)
    changes = load_changes()
    change_lines = build_change_lines(changes)
    advices = build_advices(gaps, resume, jd, rows)
    conclusion = build_conclusion(resume_scores)

    w = []
    a = w.append
    # 第1-3块：总体匹配 + 结论 + 四维度
    a("总体匹配")
    a(f"{overall} / 100")
    a(conclusion)
    a("")
    a("技能")
    a(f"{resume_scores['skill']:g} / 10")
    a("")
    a("内容")
    a(f"{resume_scores['content']:g} / 10")
    a("")
    a("行业")
    a(f"{resume_scores['industry']:g} / 10")
    a("")
    a("资历")
    a(f"{resume_scores['seniority']:g} / 10")
    a("")
    # 第4块：命中项
    a("✅")
    a("命中项（JD 要求 → 简历证据）")
    w.extend(hits)
    a("")
    # 第5块：缺口项
    a("⚠️")
    a("缺口项（JD 要求但简历未体现）")
    w.extend(gap_lines)
    a("")
    # 第6块：改动说明
    a("改动说明")
    w.extend(change_lines)
    a("")
    # 第7块：补充建议
    a("补充建议")
    for i, adv in enumerate(advices, 1):
        a(f"{i}. {adv}")

    report = "\n".join(w)
    OUT_PATH.write_text(report, encoding="utf-8")

    # 控制台输出
    print("=" * 72)
    print(f"匹配报告生成（规则+模板，无 LLM）｜简历 {resume['resume_id']} × 岗位 {jd['jd_id']}")
    print("=" * 72)
    print(f"总体匹配 {overall}/100 ｜ 技能 {resume_scores['skill']:g} 内容 "
          f"{resume_scores['content']:g} 行业 {resume_scores['industry']:g} "
          f"资历 {resume_scores['seniority']:g}")
    print(f"结论：{conclusion}")
    print(f"命中项 {len(hits)} 条 / 缺口项 {len(gap_lines)} 条 / "
          f"改动说明 {len(changes)} 条 changes / 补充建议 {len(advices)} 条")
    print(f"报告已写入: {OUT_PATH}")


if __name__ == "__main__":
    run()
