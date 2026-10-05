# -*- coding: utf-8 -*-
"""环节G：Source Mapping 四道门禁校验（纯规则，无 LLM）。

逻辑提炼自 tests/gate_test.py（不改校验规则，只做产品化封装、去掉写报告）。

Gate 1 白名单: source_ids 必须都在 source_store 中
Gate 2 黑名单: 不允许新增原文没有的公司名/时间/职位等专有名词
Gate 3 可解析: operation 必须在允许列表内，text 非空（omit 除外）
Gate 4 原文匹配: ① 数字比对 ② 贡献动词门槛 ③ 事实性名词；不通过则回退原文

产品函数: validate_changes(changes, source_store) -> dict（返回通过/拦截的 changes）
"""
import copy
import re

ALLOWED_OPERATIONS = {"verbatim", "compress", "reword", "reorder", "merge", "demote", "omit"}

# 贡献动词等级（数字越大贡献越高）
VERB_LEVELS = {
    "支持": 1, "协助": 1,
    "参与": 2,
    "负责": 3,
    "推动": 4,
    "主导": 5,
}

# 技术名词词典（Gate4 事实性名词检查）
TECH_NOUNS = [
    "Java", "Go", "Golang", "Python", "JavaScript", "TypeScript", "C++", "Rust", "PHP",
    "Spring", "Spring Boot", "Spring Cloud", "MyBatis", "MyBatis-Plus", "SSM", "Hibernate",
    "MySQL", "PostgreSQL", "Oracle", "MongoDB", "Redis", "Kafka", "RabbitMQ", "RocketMQ",
    "Elasticsearch", "ES", "Lucene", "Solr",
    "Docker", "Kubernetes", "K8s", "Jenkins", "Git", "Maven", "Gradle",
    "Linux", "Nginx", "Tomcat", "Dubbo", "Zookeeper", "Nacos", "Sentinel",
    "RESTful", "gRPC", "HTTP", "TCP/IP", "WebSocket", "JWT", "OAuth",
    "POI", "EasyExcel", "LangChain",
]

# 黑名单关键词（公司/职位类专有名词）
BLACKLIST_KW = ["公司", "工程师", "经理", "总监", "主管", "架构师", "CTO", "CEO"]


def get_source_text(source_store, source_ids):
    """拼接 source_ids 对应的原文。"""
    return " ".join(source_store.get(sid, "") for sid in source_ids)


# ============ Gate 1：白名单 ============

def gate1_whitelist(source_store, change):
    for sid in change["source_ids"]:
        if sid not in source_store:
            return False, f"source_id 不存在: {sid}"
    return True, ""


# ============ Gate 2：黑名单 ============

def gate2_blacklist(source_store, change):
    text = change["text"]
    if not text:  # text 为空/None（如 omit）时无新增专有名词可查
        return True, ""
    source_text = get_source_text(source_store, change["source_ids"])
    time_pat = re.compile(r"\d{4}\.\d{1,2}|\d{4}年|\d{1,2}年|\d+个月|\d+天")
    text_times = time_pat.findall(text)
    source_times = time_pat.findall(source_text)
    extra = [t for t in text_times if t not in source_times]
    if extra:
        return False, f"新增时间专有名词: {extra}"
    for kw in BLACKLIST_KW:
        if kw in text and kw not in source_text:
            return False, f"新增专有名词「{kw}」"
    return True, ""


# ============ Gate 3：可解析 ============

def gate3_parseable(change):
    if change.get("operation") not in ALLOWED_OPERATIONS:
        return False, f"operation 不合法: {change.get('operation')}"
    if change.get("operation") == "omit":
        return True, ""
    if not change.get("text", "").strip():
        return False, "text 为空"
    return True, ""


# ============ Gate 4：原文匹配 ============

def extract_numbers(text):
    """提取文本中所有数字（含小数）。"""
    return re.findall(r"\d+(?:\.\d+)?", text)


def get_max_verb_level(text):
    """提取文本最高贡献动词等级，排除「不+动词」的否定形式。"""
    negated = set(re.findall(r"不(支持|协助|参与|负责|推动|主导)", text))
    levels = []
    for verb, lvl in VERB_LEVELS.items():
        if verb in text and verb not in {f"不{v}" for v in negated}:
            idx = text.find(verb)
            if idx > 0 and text[idx - 1] == "不":
                continue
            levels.append(lvl)
    return max(levels) if levels else 0


def extract_tech_nouns(text):
    """提取文本中的技术名词（大小写不敏感）。"""
    found = []
    lower = text.lower()
    for noun in TECH_NOUNS:
        if noun.lower() in lower:
            found.append(noun)
    return found


def gate4_source_match(source_store, change):
    """返回 (通过, 原因, 是否回退)。不通过时把 text 回退为原文。"""
    if change.get("operation") == "omit" or not change.get("text"):
        return True, "", False
    source_text = get_source_text(source_store, change["source_ids"])
    text = change["text"]
    reasons = []

    # ① 数字比对
    text_nums = extract_numbers(text)
    source_nums = extract_numbers(source_text)
    extra_nums = [n for n in text_nums if n not in source_nums]
    if extra_nums:
        reasons.append(f"新增数字 {extra_nums}")

    # ② 贡献动词门槛
    text_verb = get_max_verb_level(text)
    source_verb = get_max_verb_level(source_text)
    if text_verb > source_verb:
        verb_word = next(v for v, lvl in VERB_LEVELS.items()
                         if lvl == text_verb and v in text)
        reasons.append(f"贡献动词「{verb_word}」超出原文允许等级")

    # ③ 事实性名词
    text_tech = extract_tech_nouns(text)
    source_tech = extract_tech_nouns(source_text)
    extra_tech = [t for t in text_tech if t not in source_tech]
    if extra_tech:
        reasons.append(f"新增技术名词 {extra_tech}")

    if reasons:
        change["text"] = source_text  # 回退原文
        return False, "; ".join(reasons), True
    return True, "", False


# ============ 单条 change 校验 ============

def validate_change(source_store, change):
    """依次执行四道门禁。返回 dict: {passed, gate, reason, rolled_back, final_text}。"""
    original_text = change["text"]
    ok, reason = gate1_whitelist(source_store, change)
    if not ok:
        return {"passed": False, "gate": 1, "reason": f"Gate1 白名单: {reason}",
                "rolled_back": False, "final_text": original_text}

    ok, reason = gate2_blacklist(source_store, change)
    if not ok:
        return {"passed": False, "gate": 2, "reason": f"Gate2 黑名单: {reason}",
                "rolled_back": False, "final_text": original_text}

    ok, reason = gate3_parseable(change)
    if not ok:
        return {"passed": False, "gate": 3, "reason": f"Gate3 可解析: {reason}",
                "rolled_back": False, "final_text": original_text}

    ok, reason, rolled_back = gate4_source_match(source_store, change)
    if not ok:
        return {"passed": False, "gate": 4, "reason": f"Gate4 原文匹配: {reason}",
                "rolled_back": rolled_back, "final_text": change["text"]}

    return {"passed": True, "gate": None, "reason": "",
            "rolled_back": False, "final_text": original_text}


# ============ 产品入口 ============

def validate_changes(changes, source_store):
    """校验整批 changes（深拷贝，不改输入）。

    返回:
        {
          "total": 总数, "passed": 通过数, "rejected": 拦截数,
          "results":        每条 {output_id,group_id,passed,gate,reason,rolled_back},
          "passed_changes": 通过的 change 列表（Gate4 回退后的文本会带在 rejected 里）,
          "rejected_changes": 拦截的 change 列表（Gate4 不通过时 text 已回退为原文）
        }
    """
    results, passed_changes, rejected_changes = [], [], []
    for raw_change in changes:
        change = copy.deepcopy(raw_change)
        res = validate_change(source_store, change)
        results.append({
            "output_id": change.get("output_id"),
            "group_id": change.get("group_id"),
            "passed": res["passed"],
            "gate": res["gate"],
            "reason": res["reason"],
            "rolled_back": res["rolled_back"],
        })
        if res["passed"]:
            passed_changes.append(change)
        else:
            rejected_changes.append(change)

    return {
        "total": len(changes),
        "passed": len(passed_changes),
        "rejected": len(rejected_changes),
        "results": results,
        "passed_changes": passed_changes,
        "rejected_changes": rejected_changes,
    }
