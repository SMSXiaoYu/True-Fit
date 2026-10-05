# -*- coding: utf-8 -*-
"""
四维度相关性打分算法验证脚本
输入: data/extracted/resumes/A_backend_5y.json（8 段经历 E1-E8）, data/extracted/jds/JD1_backend.json
输出: 控制台打印 + data/output/score_result.md

四个维度（各 0-10）:
  1. 技能   = JD skills 与经历 tech/actions/skills_declared（三级兜底）的命中数占比 * 10
  2. 内容   = JD responsibilities 关键词在经历 actions(+role) 中的命中率 * 10
  3. 行业   = JD industry.required 与经历 industry 的人工映射相似度
  4. 资历   = JD level.years 与经历 seniority 的档位距离
加权: 技能20% / 内容45% / 行业15% / 资历10%
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RESUME_PATH = BASE / "data" / "extracted" / "resumes" / "A_backend_5y.json"
JD_PATH = BASE / "data" / "extracted" / "jds" / "JD1_backend.json"
OUT_PATH = BASE / "data" / "output" / "score_result.md"

WEIGHTS = {"skill": 0.20, "content": 0.45, "industry": 0.15, "seniority": 0.10}

# 硬信号列表（来自 JD1_backend 人工标注，对应 JD 职责条目名）
HARD_SIGNALS = ["负责AIAgent后端服务开发"]

# ============ 维度1：技能同义词归一词典 ============
# key = JD 技能项；value = 经历 tech 中视为命中的关键词（小写匹配）
SKILL_SYNONYMS = {
    "Java/Go": ["java", "spring", "mybatis", "ssm", "jvm", "golang", "go语言"],
    "MySQL": ["mysql"],
    "Redis": ["redis"],
    "分布式基础": ["分布式", "微服务", "spring cloud", "rabbitmq", "kafka",
                "elasticsearch", "es", "分库", "分表", "dubbo", "zookeeper", "消息队列"],
    "Linux": ["linux"],
    "Agent/大模型应用开发经验": ["agent", "llm", "大模型", "智能体", "langchain", "向量", "对话链路"],
}

# ============ 维度2：职责关键词词典（key = JD responsibilities 条目名） ============
RESP_KEYWORDS = {
    "负责AIAgent后端服务开发": ["agent", "智能体", "大模型", "llm"],
    "实现智能体调度、工具调用、记忆模块、对话链路等功能开发": ["调度", "工具调用", "记忆模块", "对话链路"],
    "业务后端接口开发": ["接口", "api", "crud", "开发"],
    "系统优化": ["优化", "重构", "改造", "性能", "降到", "缩短", "提升"],
    "线上问题排查，保障服务稳定运行": ["线上", "故障", "告警", "排查", "压测", "值守", "投诉"],
    "参与技术方案设计": ["设计", "方案", "定义", "状态机", "架构"],
    "跟进大模型相关技术落地": ["大模型", "llm"],
}

# ============ 维度3：行业相似度人工映射 ============
# JD required 为空时给中位 5；非空时走映射。分值档位: 同行业8-10 / 相关5-7 / 弱相关2-4 / 跨行业0-1
INDUSTRY_MAP = {
    "电商": (3, "弱相关：互联网交易场景，有后端技术接口场景，但无 AI/大模型业务"),
    "SaaS": (3, "弱相关：软件服务行业，工程能力可迁移，但非 AI/大模型方向"),
    "制造业信息化": (2, "弱相关：传统制造业信息化，与AI应用差距较大"),
    "教育培训": (0, "跨行业：市场推广实习，非技术岗，与 AI 后端完全不相关"),
}

# ============ 维度4：资历档位映射（JD 要求 1-3 年） ============
# 完全匹配8-10 / 接近5-7 / 偏低偏高2-4 / 差太远0-1
SENIORITY_MAP = {
    "独立负责": (8, "完全匹配：独立负责完整模块/系统，符合 1-3 年经验要求"),
    "核心开发": (7, "接近：核心开发资历略高于 1-3 年区间中段"),
    "参与": (5, "偏低：以参与为主，独立负责程度不足"),
    "实习生": (1, "差太远：在校实习，非正式后端资历"),
}

# ============ 技术岗判断（skills_declared 兜底的前置约束） ============
TECH_ROLE_KW = ["开发", "工程师", "后端", "前端", "全栈", "架构", "测试", "运维", "数据"]
TECH_INDUSTRY_KW = ["互联网", "saas", "电商", "科技", "软件", "ai", "数据"]
NON_TECH_TECH = {"excel", "word", "ppt", "powerpoint", "outlook", "visio"}


def is_technical_role(exp):
    """判断该经历是否涉及技术岗（满足任一即可）：role 含技术词 / industry 技术相关 / tech 非空（过滤办公软件）。"""
    role = exp.get("role") or ""
    industry = exp.get("industry") or ""
    tech = [t for t in (exp.get("tech") or []) if t.lower() not in NON_TECH_TECH]
    reasons = []
    if any(k in role for k in TECH_ROLE_KW):
        reasons.append("role含技术词")
    if any(k in industry.lower() for k in TECH_INDUSTRY_KW):
        reasons.append(f"industry({industry})属技术相关行业")
    if tech:
        reasons.append(f"tech有技术栈({len(tech)}项)")
    return len(reasons) > 0, ("、".join(reasons) if reasons else "无技术岗信号")


def load_data():
    resume = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    jd = json.loads(JD_PATH.read_text(encoding="utf-8"))
    return resume, jd


def _kw_match(kw, text_lower):
    """关键词命中：英文/数字要求词边界（避免 'es' 误匹配 'redis'），中文允许子串。"""
    kw = kw.lower()
    if re.fullmatch(r"[a-z0-9 ./#+-]+", kw):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text_lower) is not None
    return kw in text_lower


def _hit_keywords(keywords, text):
    """返回 text 中命中的关键词列表（大小写不敏感）。"""
    t = text.lower()
    return [kw for kw in keywords if _kw_match(kw, t)]


def _skill_hits_in_text(jd_skills, text):
    """在给定文本中按 SKILL_SYNONYMS 关键词匹配 JD 技能，返回 {item: [命中关键词]}。"""
    hits = {}
    for s in jd_skills:
        item = s["item"]
        syns = SKILL_SYNONYMS.get(item, [item.lower()])
        mk = _hit_keywords(syns, text)
        if mk:
            hits[item] = mk
    return hits


def score_skill(jd_skills, exp, declared_hard=None):
    """
    技能分（三级兜底）：命中的不同 JD 技能数 / JD 技能总数 * 10。
    第1级 tech 字段；若命中率 < 0.5 触发第2级 actions 补抽；
    若仍 < 0.5 触发第3级 skills_declared 兜底（仅技术岗经历触发）。
    三级合并去重后计算最终命中数。
    """
    total = len(jd_skills)
    tech_list = exp.get("tech", [])

    # 第1级：tech 字段匹配
    tech_hits = {}
    for s in jd_skills:
        item = s["item"]
        syns = SKILL_SYNONYMS.get(item, [item.lower()])
        matched_tech = [t for t in tech_list if any(_kw_match(k, t.lower()) for k in syns)]
        if matched_tech:
            tech_hits[item] = matched_tech

    # 第2级：actions 补抽（tech 命中率不足时触发）
    action_hits = {}
    if len(tech_hits) / total < 0.5:
        actions_text = " ".join(exp.get("actions", []))
        action_hits = _skill_hits_in_text(jd_skills, actions_text)

    # 第3级：skills_declared 兜底（前两级合并命中率仍不足时触发，需通过技术岗判断）
    declared_hits = {}
    declared_reason = ""
    merged_so_far = len(set(tech_hits) | set(action_hits))
    if declared_hard and merged_so_far / total < 0.5:
        is_tech_role, gate_reason = is_technical_role(exp)
        if is_tech_role:
            declared_text = " ".join(declared_hard)
            declared_hits = _skill_hits_in_text(jd_skills, declared_text)
            declared_reason = f"触发简历级兜底（技术岗：{gate_reason}）"
        else:
            declared_reason = f"未触发兜底（非技术岗：{gate_reason}）"

    # 三级合并去重
    all_hits = set(tech_hits) | set(action_hits) | set(declared_hits)
    score = round(len(all_hits) / total * 10, 1)

    return score, {
        "total": total,
        "tech_hits": tech_hits,
        "tech_hit_count": len(tech_hits),
        "action_hits": action_hits,
        "action_hit_count": len(action_hits),
        "declared_hits": declared_hits,
        "declared_hit_count": len(declared_hits),
        "declared_reason": declared_reason,
        "final_hit_count": len(all_hits),
        "hits": {  # 合并明细：优先展示 tech，其次 actions，最后 declared
            **{k: ("tech", v) for k, v in tech_hits.items()},
            **{k: ("actions", v) for k, v in action_hits.items() if k not in tech_hits},
            **{k: ("declared", v) for k, v in declared_hits.items()
               if k not in tech_hits and k not in action_hits},
        },
        "tech": tech_list,
    }


def score_content(jd_resps, exp):
    """内容分：命中的职责条数 / 职责总数 * 10。扫描 actions + role。"""
    text = (exp.get("role", "") or "") + " " + " ".join(exp.get("actions", []))
    hits = {}
    for r in jd_resps:
        item = r["item"]
        kws = RESP_KEYWORDS.get(item, [])
        mk = _hit_keywords(kws, text)
        if mk:
            hits[item] = mk
    total = len(jd_resps)
    score = round(len(hits) / total * 10, 1)
    return score, {"total": total, "hit_count": len(hits), "hits": hits}


def score_industry(jd, exp):
    """行业分：required 为空给中位 5；否则走人工映射。"""
    required = jd["requirements"]["industry"].get("required")
    ind = exp.get("industry")
    if not required:
        return 5.0, {"rule": "JD 无行业硬性要求，默认中位分 5", "industry": ind}
    val, reason = INDUSTRY_MAP.get(ind, (0, "跨行业：映射表中无对应行业"))
    return float(val), {"required": required, "industry": ind, "reason": reason}


def score_seniority(jd, exp):
    """资历分：按 seniority 档位映射。"""
    sen = exp.get("seniority")
    val, reason = SENIORITY_MAP.get(sen, (0, "未知档位"))
    return float(val), {"required_years": jd["requirements"]["level"]["years"],
                        "seniority": sen, "reason": reason}


def weighted(scores):
    return round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS), 2)


# ============ 调用4：分层算法 ============
def role_score(role):
    """role 打分：独立负责/主导→3；参与/协助→2；实习/兼职→1；边角料→0。"""
    if not role:
        return 0, "未提供 role → 0"
    if "实习" in role or "兼职" in role:
        return 1, "含「实习/兼职」→ 1"
    if "独立负责" in role or "主导" in role:
        return 3, "含「独立负责/主导」→ 3"
    if "负责" in role:
        return 3, "含「负责」→ 3"
    if "参与" in role or "协助" in role or "之一" in role:
        return 2, "含「参与/协助/之一」→ 2"
    return 0, "边角料描述 → 0"


def actions_count_score(n):
    """actions 数量打分：≥4→3；2-3→2；1→1；0→0。"""
    if n >= 4:
        return 3, f"{n} 条（≥4）→ 3"
    if n >= 2:
        return 2, f"{n} 条（2-3）→ 2"
    if n == 1:
        return 1, "1 条 → 1"
    return 0, "0 条 → 0"


def calc_quality_score(exp):
    """质量分 = role(0-3) + actions数(0-3) + 有量化(0-2) + 有定性结果(0-2)，范围 0-10。"""
    rs, r_detail = role_score(exp.get("role", ""))
    ac = len(exp.get("actions", []))
    acs, ac_detail = actions_count_score(ac)
    metrics = exp.get("metrics", []) or []
    results = exp.get("results", []) or []
    has_metrics = 2 if metrics else 0
    has_metrics_detail = f"metrics {len(metrics)} 条 → {has_metrics}"
    has_qual = 1 if results and not metrics else 0
    has_qual_detail = f"results {len(results)} 条 + metrics 为空 → {has_qual}"
    total = rs + acs + has_metrics + has_qual
    detail = {
        "role": rs, "role_detail": r_detail,
        "actions_count": ac, "actions_score": acs, "actions_detail": ac_detail,
        "has_metrics": has_metrics, "has_metrics_detail": has_metrics_detail,
        "has_qual": has_qual, "has_qual_detail": has_qual_detail,
        "total": total,
    }
    return total, detail


def hard_signal_boost(exp):
    """硬信号加分：遍历 actions + tech，命中硬信号任意一项 +1.5，多项累加，上限 +3。"""
    text = " ".join(exp.get("actions", [])) + " " + " ".join(exp.get("tech", []))
    hits = []
    for sig in HARD_SIGNALS:
        # 用职责关键词词典里该信号对应的关键词集合做匹配（信号名即 JD 职责名）
        kws = RESP_KEYWORDS.get(sig, [sig.lower()])
        mk = _hit_keywords(kws, text)
        if mk:
            hits.append((sig, mk))
    boost = min(len(hits) * 1.5, 3.0)
    return boost, hits


def tier_of(score):
    """分层：≥7 核心；4-7 相关；<4 其他。"""
    if score >= 7:
        return "核心"
    if score >= 4:
        return "相关"
    return "其他"


def calc_tier(scores, exp):
    """完整分层四步：原总分 → 质量分调整内容 → 硬信号加分 → 分层。"""
    original = weighted(scores)
    quality, q_detail = calc_quality_score(exp)
    adjusted_content = round(scores["content"] * (0.5 + quality / 20), 3)
    adjusted_scores = {**scores, "content": adjusted_content}
    adjusted_total = round(weighted(adjusted_scores), 3)
    boost, boost_hits = hard_signal_boost(exp)
    final = round(adjusted_total + boost, 3)
    tier = tier_of(final)
    return {
        "original": original,
        "quality": quality, "quality_detail": q_detail,
        "adjusted_content": adjusted_content,
        "adjusted_total": adjusted_total,
        "boost": boost, "boost_hits": boost_hits,
        "final": final,
        "tier": tier,
    }


def run():
    resume, jd = load_data()
    jd_skills = jd["requirements"]["skills"]
    jd_resps = jd["requirements"]["responsibilities"]
    declared_hard = resume.get("skills_declared", {}).get("hard", [])

    rows = []
    for exp in resume["experiences"]:
        s_skill, d_skill = score_skill(jd_skills, exp, declared_hard)
        s_content, d_content = score_content(jd_resps, exp)
        s_ind, d_ind = score_industry(jd, exp)
        s_sen, d_sen = score_seniority(jd, exp)
        scores = {"skill": s_skill, "content": s_content,
                  "industry": s_ind, "seniority": s_sen}
        tier = calc_tier(scores, exp)
        rows.append({
            "id": exp["id"], "title": exp["title"],
            "scores": scores, "total": weighted(scores),
            "detail": {"skill": d_skill, "content": d_content,
                       "industry": d_ind, "seniority": d_sen},
            "tier": tier,
        })

    lines = []
    w = lines.append
    w("# 四维度相关性打分 · 算法报告\n")
    w(f"- 简历：`{resume['resume_id']}`（{resume['basic_info']['name']}），共 {len(resume['experiences'])} 段经历")
    w(f"- 岗位：`{jd['jd_id']}`（{jd['title']}）")
    w("- 权重：技能 20% / 内容 45% / 行业 15% / 资历 10%\n")

    # ---- 一、总览表 ----
    w("## 一、算法打分总览\n")
    w("| 编号 | 经历 | 技能 | 内容 | 行业 | 资历 | 加权总分 |")
    w("|---|---|---|---|---|---|---|")
    for r in rows:
        s = r["scores"]
        w(f"| {r['id']} | {r['title']} | {s['skill']} | {s['content']} | "
          f"{s['industry']:.0f} | {s['seniority']:.0f} | **{r['total']}** |")
    w("")

    # ---- 二、逐条计算明细 ----
    w("## 二、每条经历的分数怎么算出来的\n")
    for r in rows:
        s, d = r["scores"], r["detail"]
        w(f"### {r['id']} {r['title']}\n")
        # 技能（三级兜底）
        sk = d["skill"]
        w(f"**技能 = {s['skill']}**（最终命中 {sk['final_hit_count']}/{sk['total']} 个 JD 技能 → "
          f"{sk['final_hit_count']}/{sk['total']}×10）")
        w(f"- 三级命中：tech {sk['tech_hit_count']} 项 / actions 补抽 "
          f"{sk['action_hit_count']} 项 / skills_declared 兜底 {sk['declared_hit_count']} 项")
        w(f"- 经历 tech 列表：{', '.join(sk['tech']) if sk['tech'] else '（空）'}")
        if sk.get("declared_reason"):
            w(f"- 兜底判定：{sk['declared_reason']}")
        if sk["hits"]:
            for item, (src, evidence) in sk["hits"].items():
                src_label = {"tech": "tech", "actions": "actions 补抽",
                             "declared": "skills_declared 兜底"}[src]
                w(f"- ✅ `{item}` ← [{src_label}] 命中：{', '.join(evidence)}")
        final_items = set(sk["tech_hits"]) | set(sk["action_hits"]) | set(sk["declared_hits"])
        missed = [x["item"] for x in jd_skills if x["item"] not in final_items]
        w(f"- ⏭️ 未命中：{', '.join(missed)}\n")
        # 内容
        ct = d["content"]
        w(f"**内容 = {s['content']}**（命中 {ct['hit_count']}/{ct['total']} 条 JD 职责 → "
          f"{ct['hit_count']}/{ct['total']}×10）")
        if ct["hits"]:
            for item, kws in ct["hits"].items():
                w(f"- ✅ `{item}` ← 命中关键词：{', '.join(kws)}")
        missed_r = [x["item"] for x in jd_resps if x["item"] not in ct["hits"]]
        w(f"- ⏭️ 未命中：{', '.join(missed_r)}\n")
        # 行业
        ind = d["industry"]
        if "required" in ind:
            w(f"**行业 = {s['industry']:.0f}**（JD 要求：{', '.join(ind['required'])}；"
              f"经历行业：{ind['industry']}）")
            w(f"- {ind['reason']}\n")
        else:
            w(f"**行业 = {s['industry']:.0f}**（{ind['rule']}；经历行业：{ind['industry']}）\n")
        # 资历
        se = d["seniority"]
        w(f"**资历 = {s['seniority']:.0f}**（JD 要求：{se['required_years']}；"
          f"经历档位：{se['seniority']}）")
        w(f"- {se['reason']}\n")
        w(f"**加权总分 = {s['skill']}×0.20 + {s['content']}×0.45 + "
          f"{s['industry']:.0f}×0.15 + {s['seniority']:.0f}×0.10 = {r['total']}**\n")

    # ===== 三、调用4：分层算法 =====
    w("\n---\n")
    w("# 三、调用4：分层算法\n")
    w("四步流程：① 4 维度加权原总分 → ② 质量分调整内容分后重算总分 "
      "→ ③ 硬信号加分 → ④ 按阈值分层（≥7 核心 / 4-7 相关 / <4 其他）。\n")
    w(f"硬信号列表：`{HARD_SIGNALS}`\n")

    w("## 3.1 分层结果总览\n")
    w("| 编号 | 经历 | 技能 | 内容 | 行业 | 资历 | 原总分 | 质量分 | "
      "调整后内容 | 调整后总分 | 硬信号加分 | 最终总分 | 分层 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        s, t = r["scores"], r["tier"]
        w(f"| {r['id']} | {r['title']} | {s['skill']} | {s['content']} | "
          f"{s['industry']:.0f} | {s['seniority']:.0f} | {t['original']} | "
          f"{t['quality']} | {t['adjusted_content']} | {t['adjusted_total']} | "
          f"+{t['boost']} | **{t['final']}** | **{t['tier']}** |")
    w("")

    w("## 3.2 每条经历质量分怎么算出来的\n")
    for r in rows:
        t = r["tier"]
        q = t["quality_detail"]
        w(f"### {r['id']} {r['title']}\n")
        w(f"- role = `{q['role_detail']}`（{q['role']} 分）")
        w(f"- actions 数量 = {q['actions_detail']}（{q['actions_score']} 分）")
        w(f"- 有量化 = {q['has_metrics_detail']}（{q['has_metrics']} 分）")
        w(f"- 有定性结果 = {q['has_qual_detail']}（{q['has_qual']} 分）")
        w(f"- **质量分合计 = {q['total']}**")
        adj = t["adjusted_content"]
        w(f"- 调整后内容分 = {r['scores']['content']} × (0.5 + {q['total']}/20) = {adj}")
        w(f"- 调整后总分 = {r['scores']['skill']}×0.2 + {adj}×0.45 + "
          f"{r['scores']['industry']:.0f}×0.15 + {r['scores']['seniority']:.0f}×0.10 "
          f"= {t['adjusted_total']}")
        if t["boost_hits"]:
            sigs = "、".join(f"{sig}（命中：{','.join(kws)}）" for sig, kws in t["boost_hits"])
            w(f"- 硬信号加分 = +{t['boost']}（命中：{sigs}）")
        else:
            w(f"- 硬信号加分 = +{t['boost']}（actions/tech 中未命中硬信号关键词）")
        w(f"- **最终总分 = {t['adjusted_total']} + {t['boost']} = {t['final']} → "
          f"分层【{t['tier']}】**\n")

    w("## 3.3 分层结论\n")
    dist = {"核心": 0, "相关": 0, "其他": 0}
    for r in rows:
        dist[r["tier"]["tier"]] += 1
    w(f"1. 分层分布：核心 {dist['核心']} 条 / 相关 {dist['相关']} 条 / 其他 {dist['其他']} 条"
      f"（共 {len(rows)} 条）。")
    w("2. 质量分机制对内容分起到「按经历质量缩放」的作用："
      "高质量经历（独立负责 + 多条 actions + 量化结果）内容分最多放大到 1.0×，"
      "低质量经历（实习/无量化）内容分最低压缩到 0.5×。")
    w("3. 硬信号加分在本数据集中未触发（没有经历涉及 AI Agent 业务），"
      "若简历中出现 Agent/大模型相关经历会按命中项 +1.5/项（上限 +3），直接影响分层。")

    # ===== 四、技能兜底前后对比（三级兜底结果 vs 旧的纯 tech 结果） =====
    # 旧结果：只看 tech 字段的命中数
    old_skill_scores = {}
    for exp in resume["experiences"]:
        tech_list = exp.get("tech", [])
        tech_hits = set()
        for s in jd_skills:
            item = s["item"]
            syns = SKILL_SYNONYMS.get(item, [item.lower()])
            if any(_kw_match(k, t.lower()) for t in tech_list for k in syns):
                tech_hits.add(item)
        old_skill_scores[exp["id"]] = round(len(tech_hits) / len(jd_skills) * 10, 1)

    # 旧分层：用旧技能分重算（内容/行业/资历不变，质量分调整同）
    old_tiers = {}
    for r in rows:
        eid = r["id"]
        old_skill = old_skill_scores[eid]
        old_scores = {**r["scores"], "skill": old_skill}
        old_tier = calc_tier(old_scores, resume["experiences"][
            next(i for i, e in enumerate(resume["experiences"]) if e["id"] == eid)])
        old_tiers[eid] = (old_skill, old_tier["final"], old_tier["tier"])

    w("\n---\n")
    w("# 四、技能兜底机制：前后对比\n")
    w("技能分计算从「只看 tech 字段」升级为「三级兜底」："
      "tech → actions 补抽（命中率<0.5 触发）→ skills_declared 兜底（仍<0.5 触发，"
      "且需通过技术岗判断：role 含技术词 / industry 技术相关 / tech 非空，任一满足）。\n")

    w("## 4.1 技能分前后对比\n")
    w("| 编号 | 原技能分(纯tech) | 新技能分(三级兜底) | 变化 | 新增命中来源 |")
    w("|---|---|---|---|---|")
    for r in rows:
        eid = r["id"]
        old_s = old_skill_scores[eid]
        new_s = r["scores"]["skill"]
        diff = round(new_s - old_s, 1)
        sk = r["detail"]["skill"]
        added = []
        if sk["action_hit_count"]:
            added.append(f"actions补抽{sk['action_hit_count']}项")
        if sk["declared_hit_count"]:
            added.append(f"skills_declared兜底{sk['declared_hit_count']}项")
        added_str = "、".join(added) if added else "无"
        w(f"| {eid} | {old_s} | {new_s} | {diff:+.1f} | {added_str} |")
    w("")

    w("## 4.2 最终总分与分层前后对比\n")
    w("| 编号 | 原技能分 | 新技能分 | 原最终总分 | 新最终总分 | 原分层 | 新分层 | 是否变层 |")
    w("|---|---|---|---|---|---|---|---|")
    tier_changed = 0
    for r in rows:
        eid = r["id"]
        old_s = old_skill_scores[eid]
        new_s = r["scores"]["skill"]
        old_final, old_tier = old_tiers[eid][1], old_tiers[eid][2]
        new_final, new_tier = r["tier"]["final"], r["tier"]["tier"]
        changed = old_tier != new_tier
        tier_changed += 1 if changed else 0
        mark = "是" if changed else "否"
        w(f"| {eid} | {old_s} | {new_s} | {old_final} | {new_final} | "
          f"{old_tier} | {new_tier} | {mark} |")
    w("")

    w("## 4.3 兜底结论\n")
    w("1. 三级兜底仅在 tech 命中率不足 50% 时逐级触发，不会对 tech 字段充足的经历造成过度补偿，"
      "保证经历级信号优先。")
    beneficiaries = [r for r in rows if r["detail"]["skill"]["declared_hit_count"] > 0]
    if beneficiaries:
        w(f"2. skills_declared 兜底生效：{'、'.join(r['id'] for r in beneficiaries)} "
          "的技能分由简历级技能补全（兜底明细见第二章）。")
    blocked = [r for r in rows
               if (r["detail"]["skill"].get("declared_reason") or "").startswith("未触发")]
    if blocked:
        w(f"3. 技术岗约束生效：{'、'.join(r['id'] + ' ' + r['title'] for r in blocked)} "
          "为非技术岗经历，即使简历级技能可匹配也不触发兜底，避免技能分失真。")
    w(f"4. 兜底后共 {tier_changed} 条经历的分层相比「纯 tech」口径发生变化（见 4.2）。")

    report = "\n".join(lines)
    OUT_PATH.write_text(report, encoding="utf-8")

    # 控制台输出
    print("=" * 76)
    print("四维度相关性打分结果（权重: 技能20% / 内容45% / 行业15% / 资历10%）")
    print("=" * 76)
    print(f"{'编号':<5}{'经历':<12}{'技能':>6}{'内容':>6}{'行业':>6}{'资历':>6}{'加权总分':>8}")
    print("-" * 76)
    for r in rows:
        s = r["scores"]
        print(f"{r['id']:<5}{r['title']:<12}{s['skill']:>6}{s['content']:>6}"
              f"{s['industry']:>6.0f}{s['seniority']:>6.0f}{r['total']:>8}")
    print("-" * 76)

    # 分层结果输出
    print("\n" + "=" * 80)
    print("调用4：分层结果（质量分调整内容 + 硬信号加分，≥7核心 / 4-7相关 / <4其他）")
    print("=" * 80)
    print(f"{'编号':<5}{'经历':<12}{'原总分':>7}{'质量分':>7}{'调整内容':>8}"
          f"{'调整总分':>9}{'硬信号':>7}{'最终':>7}{'分层':>5}")
    print("-" * 80)
    for r in rows:
        t = r["tier"]
        print(f"{r['id']:<5}{r['title']:<12}{t['original']:>7}{t['quality']:>7}"
              f"{t['adjusted_content']:>8}{t['adjusted_total']:>9}"
              f"+{t['boost']:<6}{t['final']:>7}{t['tier']:>5}")
    print("-" * 80)
    print(f"\n兜底后相比纯 tech 口径发生分层变化的经历: {tier_changed} 条")
    print(f"报告已写入: {OUT_PATH}")


if __name__ == "__main__":
    run()
