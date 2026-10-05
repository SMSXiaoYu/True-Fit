# -*- coding: utf-8 -*-
"""调用4：分层（纯规则，无 LLM）。

逻辑提炼自 tests/score_test.py 的分层部分（不改算法，只做产品化封装、去掉写报告）。

四步流程：
  ① 四维度加权原总分
  ② 质量分（0-10）调整内容分：content × (0.5 + quality/20)
  ③ 硬信号加分：命中每项 +1.5，上限 +3
  ④ 阈值分层：≥7 核心 / 4-7 相关 / <4 其他

产品函数: tier_experiences(scored, hard_signals) -> dict
（scored 为 step3_score.score_experiences 的返回值，原地补充 tier 字段后返回）
"""
try:  # 兼容「包导入」与「脚本直跑」两种方式
    from .step3_score import weighted, _hit_keywords, HARD_SIGNALS, RESP_KEYWORDS
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from step3_score import weighted, _hit_keywords, HARD_SIGNALS, RESP_KEYWORDS


def role_score(role):
    """role 打分：独立负责/主导/负责→3；参与/协助→2；实习/兼职→1；边角料→0。"""
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


def hard_signal_boost(exp, hard_signals=None):
    """硬信号加分：actions + tech 命中任意一项 +1.5，多项累加，上限 +3。"""
    hard_signals = hard_signals if hard_signals is not None else HARD_SIGNALS
    text = " ".join(exp.get("actions", [])) + " " + " ".join(exp.get("tech", []))
    hits = []
    for sig in hard_signals:
        kws = RESP_KEYWORDS.get(sig, [sig.lower()])
        mk = _hit_keywords(kws, text)
        if mk:
            hits.append((sig, mk))
    boost = min(len(hits) * 1.5, 3.0)
    return boost, hits


def tier_of(score):
    """分层阈值：≥7 核心；4-7 相关；<4 其他。"""
    if score >= 7:
        return "核心"
    if score >= 4:
        return "相关"
    return "其他"


def calc_tier(scores, exp, hard_signals=None, weights=None):
    """单段经历完整分层四步：原总分 → 质量分调整内容 → 硬信号加分 → 分层。"""
    hard_signals = hard_signals if hard_signals is not None else HARD_SIGNALS
    original = weighted(scores, weights)
    quality, q_detail = calc_quality_score(exp)
    adjusted_content = round(scores["content"] * (0.5 + quality / 20), 3)
    adjusted_scores = {**scores, "content": adjusted_content}
    adjusted_total = round(weighted(adjusted_scores, weights), 3)
    boost, boost_hits = hard_signal_boost(exp, hard_signals)
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


# ============ 产品入口 ============

def tier_experiences(scored, hard_signals=None):
    """对 score_experiences() 的全部打分结果做分层，原地在每行补 "tier"。

    参数:
        scored:       step3_score.score_experiences() 返回的 dict（rows 内含 exp 原文）
        hard_signals: 硬信号列表，默认 step3_score.HARD_SIGNALS
    返回:
        同一个 scored dict（rows[i] 新增 "tier" 字段），另附汇总 distribution
    """
    hard_signals = hard_signals if hard_signals is not None else HARD_SIGNALS
    weights = scored.get("weights")
    distribution = {"核心": 0, "相关": 0, "其他": 0}
    for row in scored["rows"]:
        tier = calc_tier(row["scores"], row["exp"], hard_signals, weights)
        row["tier"] = tier
        distribution[tier["tier"]] += 1
    scored["distribution"] = distribution
    return scored
