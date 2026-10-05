# -*- coding: utf-8 -*-
"""
Source Mapping 四道门禁校验脚本（Gate 1-4）
输入: data/extracted/resumes/A_backend_5y.json（source_store）
输出: 控制台打印 + data/output/gate_result.md

Gate 1 白名单: source_ids 必须都在 source_store 中
Gate 2 黑名单: 不允许新增原文没有的公司名/时间/职位等专有名词
Gate 3 可解析: operation 必须在允许列表内，text 非空
Gate 4 原文匹配（核心）:
  ① 数字比对：text 中数字必须在 source 原文中出现
  ② 贡献动词门槛：text 动词等级 ≤ source 原文最高动词等级
  ③ 事实性名词：text 中技术名词必须在 source 原文中出现
  不通过则回退原文并记录日志
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RESUME_PATH = BASE / "data" / "extracted" / "resumes" / "A_backend_5y.json"
CHANGES_PATH = BASE / "data" / "output" / "changes_A.json"
OUT_PATH = BASE / "data" / "output" / "gate_result.md"

ALLOWED_OPERATIONS = {"verbatim", "compress", "reword", "reorder", "merge", "demote", "omit"}

# 贡献动词等级（数字越大贡献越高）
VERB_LEVELS = {
    "支持": 1, "协助": 1,
    "参与": 2,
    "负责": 3,
    "推动": 4,
    "主导": 5,
}

# 技术名词词典（用于 Gate4 事实性名词检查）
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


def load_source_store():
    resume = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    return resume.get("source_store", {})


def get_source_text(source_store, source_ids):
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
    # text 为空/None（如 omit）时无新增专有名词可查
    if not text:
        return True, ""
    source_text = get_source_text(source_store, change["source_ids"])
    # 时间格式专有名词
    time_pat = re.compile(r"\d{4}\.\d{1,2}|\d{4}年|\d{1,2}年|\d+个月|\d+天")
    text_times = time_pat.findall(text)
    source_times = time_pat.findall(source_text)
    extra = [t for t in text_times if t not in source_times]
    if extra:
        return False, f"新增时间专有名词: {extra}"
    # 公司/职位关键词
    for kw in BLACKLIST_KW:
        if kw in text and kw not in source_text:
            return False, f"新增专有名词「{kw}」"
    return True, ""


# ============ Gate 3：可解析 ============
def gate3_parseable(change):
    if change.get("operation") not in ALLOWED_OPERATIONS:
        return False, f"operation 不合法: {change.get('operation')}"
    # omit 允许 text 为空/None（省略不展示，无需原文匹配）
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
    # 去掉否定形式
    negated = set(re.findall(r"不(支持|协助|参与|负责|推动|主导)", text))
    levels = []
    for verb, lvl in VERB_LEVELS.items():
        if verb in text and verb not in {f"不{v}" for v in negated}:
            # 确认不是"不负责"这种
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
    # omit / text 为空时无需原文匹配
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
        verb_word = next(v for v, l in VERB_LEVELS.items()
                         if l == text_verb and v in text)
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


# ============ 主校验流程 ============
def validate_change(source_store, change):
    """依次执行四道门禁。返回 dict: {passed, reason, rolled_back, final_text}。"""
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


# ============ 从 changes_A.json 加载 changes 数组 ============
def load_changes():
    """读取 changes_A.json，返回 test_case 列表（每条 change 包一层 expected_pass=True）。"""
    data = json.loads(CHANGES_PATH.read_text(encoding="utf-8"))
    cases = []
    for c in data["changes"]:
        desc = f"{c['operation']} {c['group_id']}（{'+'.join(c['source_ids'])}）"
        cases.append({
            "id": c["output_id"],
            "desc": desc,
            "change": c,
            "expected_pass": True,
        })
    return cases


def run():
    source_store = load_source_store()
    test_cases = load_changes()
    results = []
    for tc in test_cases:
        # 深拷贝 change，避免回退修改原用例
        change = json.loads(json.dumps(tc["change"]))
        res = validate_change(source_store, change)
        match_expected = (res["passed"] == tc["expected_pass"])
        if not tc["expected_pass"] and "expected_reason_keyword" in tc:
            match_expected = match_expected and (tc["expected_reason_keyword"] in res["reason"])
        results.append({
            "id": tc["id"], "desc": tc["desc"],
            "change": tc["change"], "result": res,
            "expected_pass": tc["expected_pass"],
            "match_expected": match_expected,
        })

    # 生成报告
    lines = []
    w = lines.append
    w("# Source Mapping 四道门禁校验报告\n")
    w(f"- 数据来源：`data/extracted/resumes/A_backend_5y.json` 的 source_store")
    w(f"- 门禁：Gate1 白名单 / Gate2 黑名单 / Gate3 可解析 / Gate4 原文匹配（数字·动词·名词）\n")

    w("## 一、source_store 相关原文\n")
    for sid in ["W1", "W2", "W3", "W4", "P1", "P2", "P3"]:
        w(f"**{sid}**：{source_store.get(sid, '（不存在）')}\n")

    w("## 二、测试用例结果\n")
    w("| 用例 | 描述 | 预期 | 实际 | 拦截原因 | 回退原文 | 与预期一致 |")
    w("|---|---|---|---|---|---|---|")
    for r in results:
        actual = "通过" if r["result"]["passed"] else "拦截"
        expected = "通过" if r["expected_pass"] else "拦截"
        reason = r["result"]["reason"] if r["result"]["reason"] else "—"
        rolled = "是" if r["result"]["rolled_back"] else "否"
        ok = "✅" if r["match_expected"] else "❌"
        w(f"| {r['id']} | {r['desc']} | {expected} | {actual} | {reason} | {rolled} | {ok} |")
    w("")

    w("## 三、逐用例详情\n")
    for r in results:
        w(f"### {r['id']} {r['desc']}\n")
        w(f"**输入 change：**")
        w(f"```json\n{json.dumps(r['change'], ensure_ascii=False, indent=2)}\n```")
        res = r["result"]
        w(f"**实际结果：** {'通过' if res['passed'] else '拦截'}")
        if res["reason"]:
            w(f"- 拦截原因：{res['reason']}")
        if res["rolled_back"]:
            w(f"- 已回退原文：`{res['final_text'][:100]}...`")
        w(f"- 与预期一致：{'✅' if r['match_expected'] else '❌'}\n")

    # 汇总
    passed = sum(1 for r in results if r["match_expected"])
    w("## 四、汇总\n")
    w(f"- 测试用例总数：{len(results)}")
    w(f"- 与预期一致：{passed}")
    w(f"- 通过率：{passed}/{len(results)} = {passed/len(results)*100:.0f}%")
    w("")
    w("## 五、门禁实现说明\n")
    w("- **Gate1 白名单**：遍历 source_ids，逐个检查是否在 source_store 中存在")
    w("- **Gate2 黑名单**：提取 text 中的时间格式（YYYY.MM 等）和公司/职位关键词，"
      "若原文未出现则拒绝")
    w("- **Gate3 可解析**：operation 必须在 {verbatim, compress, reword, reorder, merge, "
      "demote, omit} 内，text 非空")
    w("- **Gate4 原文匹配**：")
    w("  - ① 数字比对：提取 text 中所有数字，必须在 source 原文数字集合中")
    w("  - ② 动词门槛：支持/协助=1, 参与=2, 负责=3, 推动=4, 主导=5；"
      "text 动词等级不得超过原文最高等级，排除「不+动词」否定形式")
    w("  - ③ 技术名词：text 中的技术名词（Redis/Kafka/Docker 等）必须在原文出现")
    w("  - 任一项不通过 → 回退 text 为 source 原文，记录日志")

    report = "\n".join(lines)
    OUT_PATH.write_text(report, encoding="utf-8")

    # 控制台
    print("=" * 72)
    print("Source Mapping 四道门禁校验")
    print("=" * 72)
    print(f"{'用例':<8}{'描述':<16}{'预期':>6}{'实际':>6}{'一致':>6}")
    print("-" * 72)
    for r in results:
        expected = "通过" if r["expected_pass"] else "拦截"
        actual = "通过" if r["result"]["passed"] else "拦截"
        ok = "✅" if r["match_expected"] else "❌"
        print(f"{r['id']:<8}{r['desc']:<16}{expected:>6}{actual:>6}{ok:>6}")
        if not r["result"]["passed"]:
            print(f"         原因: {r['result']['reason']}")
    print("-" * 72)
    passed = sum(1 for r in results if r["match_expected"])
    print(f"\n通过率: {passed}/{len(results)} = {passed/len(results)*100:.0f}%")
    print(f"报告已写入: {OUT_PATH}")


if __name__ == "__main__":
    run()
