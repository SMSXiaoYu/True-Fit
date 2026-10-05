# -*- coding: utf-8 -*-
"""调用3：四维度相关性打分。

逻辑提炼自 tests/score_test.py；其中「内容 content」维度已升级为
bge-m3 embedding 语义相似度（src/pipeline/embedding_utils.py），
其余三维保持纯规则。

四个维度（各 0-10）:
  1. 技能 skill     = JD skills 与经历 tech/actions/skills_declared（三级兜底）命中数占比 ×10
  2. 内容 content   = JD responsibilities 与经历 actions(+role) 的语义相似度均值 ×10
                      （USE_EMBEDDING=False 时回退为关键词命中率 ×10）
  3. 行业 industry  = JD industry.required 与经历 industry 的人工映射相似度
  4. 资历 seniority = JD level.years 与经历 seniority 的档位距离
加权: 技能 20% / 内容 45% / 行业 15% / 资历 10%

产品函数: score_experiences(experiences, requirements, weights) -> dict
"""
import re

# embedding 工具：兼容「包导入」（from src.pipeline.step3_score import ...）
# 与「脚本直跑」（python src/pipeline/step3_score.py）两种场景。
# embedding_utils 顶层只依赖 numpy，重库 FlagEmbedding 在其内部懒加载，可安全顶层导入。
try:
    from .embedding_utils import encode_texts, cosine_sim_matrix
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from embedding_utils import encode_texts, cosine_sim_matrix

# 内容维度算法开关：True=embedding 语义相似度；False=关键词匹配（旧算法）
USE_EMBEDDING = True

# 默认权重（可被入参覆盖）
WEIGHTS = {"skill": 0.20, "content": 0.45, "industry": 0.15, "seniority": 0.10}

# 硬信号列表（默认值，可被入参覆盖；对应 JD 职责条目名）
HARD_SIGNALS = ["负责AIAgent后端服务开发"]

# ============ 维度1：技能同义词归一词典 ============
SKILL_SYNONYMS = {
    "Java/Go": ["java", "spring", "mybatis", "ssm", "jvm", "golang", "go语言"],
    "MySQL": ["mysql"],
    "Redis": ["redis"],
    "分布式基础": ["分布式", "微服务", "spring cloud", "rabbitmq", "kafka",
                "elasticsearch", "es", "分库", "分表", "dubbo", "zookeeper", "消息队列"],
    "Linux": ["linux"],
    "Agent/大模型应用开发经验": ["agent", "llm", "大模型", "智能体", "langchain", "向量", "对话链路"],
}

# ============ 维度2：职责关键词词典 ============
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
INDUSTRY_MAP = {
    "电商": (3, "弱相关：互联网交易场景，有后端技术接口场景，但无 AI/大模型业务"),
    "SaaS": (3, "弱相关：软件服务行业，工程能力可迁移，但非 AI/大模型方向"),
    "制造业信息化": (2, "弱相关：传统制造业信息化，与AI应用差距较大"),
    "教育培训": (0, "跨行业：市场推广实习，非技术岗，与 AI 后端完全不相关"),
}

# ============ 维度4：资历档位映射 ============
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
    """判断该经历是否涉及技术岗：role 含技术词 / industry 技术相关 / tech 非空（过滤办公软件）。"""
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
    """技能分（三级兜底）：命中的不同 JD 技能数 / JD 技能总数 ×10。"""
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
        "hits": {
            **{k: ("tech", v) for k, v in tech_hits.items()},
            **{k: ("actions", v) for k, v in action_hits.items() if k not in tech_hits},
            **{k: ("declared", v) for k, v in declared_hits.items()
               if k not in tech_hits and k not in action_hits},
        },
        "tech": tech_list,
    }


def score_content_keyword(jd_resps, exp):
    """内容分（旧算法·关键词匹配）：命中的职责条数 / 职责总数 ×10。扫描 actions + role。"""
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
    return score, {"algo": "keyword", "total": total, "hit_count": len(hits), "hits": hits}


def score_content_embedding(jd_resps, exp, jd_emb=None):
    """内容分（新算法·embedding 语义相似度，bge-m3）。

    1. JD 所有 responsibilities、该经历所有 actions 分别编码成向量
    2. 算相似度矩阵，对每条 responsibility 取它与所有 actions 的最高相似度
    3. 内容分 = 所有 responsibility 最高相似度的平均 × 10

    jd_emb 可传入预编码好的 JD 向量（多段经历复用，避免重复编码）。
    明细里保留每条职责的 max 相似度 + 命中的 action，便于与旧算法对比/排查。
    """
    jd_texts = [r["item"] for r in jd_resps]
    action_texts = list(exp.get("actions", []))
    total = len(jd_resps)

    if not action_texts:
        return 0.0, {"algo": "embedding", "total": total,
                     "max_sims": [], "hits": {}, "reason": "该经历无 actions"}

    if jd_emb is None:
        jd_emb = encode_texts(jd_texts)
    action_emb = encode_texts(action_texts)
    sim = cosine_sim_matrix(jd_emb, action_emb)

    max_sims = sim.max(axis=1)                    # 长度 = len(jd_texts)
    best_action_idx = sim.argmax(axis=1)          # 每条职责最像哪条 action

    # 明细：每条职责的最高相似度 + 对应 action
    detail = {}
    for i, r in enumerate(jd_resps):
        detail[r["item"]] = {
            "max_sim": round(float(max_sims[i]), 4),
            "best_action": action_texts[int(best_action_idx[i])],
        }

    score = round(float(max_sims.mean()) * 10, 1)
    return score, {"algo": "embedding", "total": total,
                   "avg_max_sim": round(float(max_sims.mean()), 4),
                   "hits": detail}


def score_content(jd_resps, exp, use_embedding=None, jd_emb=None):
    """内容分入口。use_embedding=None 时用模块级 USE_EMBEDDING 开关。"""
    use_embedding = USE_EMBEDDING if use_embedding is None else use_embedding
    if use_embedding:
        return score_content_embedding(jd_resps, exp, jd_emb=jd_emb)
    return score_content_keyword(jd_resps, exp)


def score_industry(requirements, exp):
    """行业分：required 为空给中位 5；否则走人工映射。"""
    required = requirements["industry"].get("required")
    ind = exp.get("industry")
    if not required:
        return 5.0, {"rule": "JD 无行业硬性要求，默认中位分 5", "industry": ind}
    val, reason = INDUSTRY_MAP.get(ind, (0, "跨行业：映射表中无对应行业"))
    return float(val), {"required": required, "industry": ind, "reason": reason}


def score_seniority(requirements, exp):
    """资历分：按 seniority 档位映射。"""
    sen = exp.get("seniority")
    val, reason = SENIORITY_MAP.get(sen, (0, "未知档位"))
    return float(val), {"required_years": requirements["level"]["years"],
                        "seniority": sen, "reason": reason}


def weighted(scores, weights=None):
    """四维度加权总分（0-10 分制）。"""
    weights = weights or WEIGHTS
    return round(sum(scores[k] * weights[k] for k in weights), 2)


# ============ 产品入口 ============

def score_experiences(experiences, requirements, weights=None, skills_declared_hard=None):
    """对全部经历打四维度分。

    参数:
        experiences:           简历 experiences 列表
        requirements:          JD requirements dict（含 skills/responsibilities/industry/level）
        weights:               四维度权重，默认技能.20/内容.45/行业.15/资历.10
        skills_declared_hard:  简历级技能声明（resume["skills_declared"]["hard"]），
                               供技能分第三级兜底使用
    返回:
        {
          "weights": {...},
          "rows": [ 每段经历一条:
            {"id","title","scores":{skill,content,industry,seniority},
             "total","detail":{skill,content,industry,seniority 各自的打分明细},
             "exp": <原经历 dict，供 step4 分层使用>}
          ]
        }
    """
    weights = weights or WEIGHTS
    jd_skills = requirements["skills"]
    jd_resps = requirements["responsibilities"]

    # embedding 模式下 JD 职责只编码一次，所有经历复用
    jd_emb = None
    if USE_EMBEDDING:
        if not jd_resps:
            # 没有 JD 职责，跳过内容维度打分（返回空）
            print("[step3] 警告：JD responsibilities 为空，内容维度全 0")
            # 直接把 experiences 的内容分设为 0（或者直接 return 空结果）
        jd_emb = encode_texts([r["item"] for r in jd_resps])

    rows = []
    for exp in experiences:
        s_skill, d_skill = score_skill(jd_skills, exp, skills_declared_hard)
        s_content, d_content = score_content(jd_resps, exp, jd_emb=jd_emb)
        s_ind, d_ind = score_industry(requirements, exp)
        s_sen, d_sen = score_seniority(requirements, exp)
        scores = {"skill": s_skill, "content": s_content,
                  "industry": s_ind, "seniority": s_sen}
        rows.append({
            "id": exp["id"], "title": exp["title"],
            "industry": exp.get("industry") or "",
            "scores": scores, "total": weighted(scores, weights),
            "detail": {"skill": d_skill, "content": d_content,
                       "industry": d_ind, "seniority": d_sen},
            "exp": exp,
        })

    return {"weights": weights, "rows": rows}
