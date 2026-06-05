# =============================================================================
# S3_call_dependency.py
# 功能：对 benchmark 测试用例目录批量生成函数调用图，并将 PyAnalyzer 与 PyCG的调用图结果与 ground truth 进行精确度对比，输出 CSV 报告。
# =============================================================================

import csv                          # 用于写出 CSV 格式的对比报告
import json                         # 解析各工具输出的 JSON 文件
import os                           # 操作系统接口：切换工作目录等
import subprocess                   # 调用外部命令（PyAnalyzer 可执行文件、PyCG）
from dataclasses import dataclass   # 用于定义数据类 CompareItem
from pathlib import Path            # 面向对象的路径操作
from typing import Tuple, Dict, List, Set, Iterable, Optional  # 类型注解


# =============================================================================
# align_test_dir：对指定目录下的每个测试用例运行 PyAnalyzer 和 PyCG，
#                 生成调用图文件，并对 PyCG 输出做路径前缀清理。
# =============================================================================
def align_test_dir(dir: Path):
    # 遍历顶层目录下的每个子目录（对应一类测试场景，如 args、calls 等）
    for sub_dir in dir.absolute().iterdir():
        os.chdir(sub_dir)  # 切换到子目录，使相对路径调用正确解析

        # 遍历子目录下的每个具体测试用例目录
        for case in sub_dir.iterdir():
            case_name = case.name  # 记录用例名称，用于后续路径替换

            if case.is_dir():  # 仅处理目录，跳过散落的文件
                # -------------------------------------------------------
                # 调用 PyAnalyzer 可执行文件（__main__.exe）对该用例进行分析：
                #   - --cfg：生成控制流图
                #   - --cg：生成调用图
                #   - --builtins：指定内置函数描述文件目录
                # -------------------------------------------------------
                subprocess.call(
                    f"__main__.exe {case} --cfg --cg --builtins F:\\Master\\Python\\AIDep-py\\test\\builtins",
                    shell=True)

                # -------------------------------------------------------
                # 调用 PyCG 对该用例的 main.py 进行分析，
                # 输出结果保存为 pycg-callgraph.json
                # -------------------------------------------------------
                subprocess.call(
                    "pycg {} -o {}"
                    .format(case.joinpath("main.py"), case.joinpath("pycg-callgraph.json")),
                    shell=True)

                # -------------------------------------------------------
                # 清理 PyCG 输出中的路径前缀：
                # PyCG 默认将调用者/被调用者写为 "caseName\\funcName" 形式，
                # 此处将 "caseName\\" 替换为空，使其与 PyAnalyzer 格式对齐。
                # -------------------------------------------------------
                case.joinpath("pycg-callgraph.json") \
                    .write_text(case.joinpath("pycg-callgraph.json")
                                .read_text().replace(f"{case_name}\\\\", ""))


import re  # 正则表达式模块，用于匹配 lambda 函数的长名称格式

# 匹配形如 "a.b.c(123)" 的 lambda 函数名（末尾带括号+数字后缀）
lambda_pattern = "(.*)\\(\d+\\)$"
compiled_pattern = re.compile(lambda_pattern)  # 预编译以提升性能

# 以下三个类型别名仅作文档说明用途（均为 int 的别名）
pyanalyzerCall = int   # PyAnalyzer 找到的调用关系数
PycgCall = int         # PyCG 找到的调用关系数
SameCall = int         # 两者共同找到的调用关系数


# =============================================================================
# remove_lambda_postfix：若函数名末尾带有 "(数字)" 形式的 lambda 后缀，
#                        则去掉该后缀并返回前缀；否则返回 None。
# 参数：longname —— 可能含后缀的函数限定名
# 返回：去掉后缀的前缀字符串，或 None（无匹配）
# =============================================================================
def remove_lambda_postfix(longname: str) -> Optional[str]:
    if matched := re.match(compiled_pattern, longname):
        removed_postfix = matched.group(1)  # 捕获第 1 组：括号前的部分
        return removed_postfix
    return None  # 不匹配则返回 None


# =============================================================================
# create_lambda_dict：从 PyAnalyzer 的 dep JSON 中提取所有匿名函数（lambda），
#                     按出现顺序编号，构建 {限定名（去模块前缀）: 序号} 的映射字典。
# 参数：case_dir         —— 当前测试用例目录（用于去除模块名前缀）
#       pyanalyzer_file  —— PyAnalyzer 输出的 dep JSON 文件路径
# 返回：lambda 名 → 序号 的字典
# =============================================================================
def create_lambda_dict(case_dir: Path, pyanalyzer_file: Path) -> Dict[str, int]:
    dep = json.loads(pyanalyzer_file.read_text())  # 加载 dep JSON
    entities = dep["variables"]   # 所有实体（函数、变量、类等）
    id_mapping = dict()           # id → 实体对象 的映射，便于后续通过 id 快速查找
    lambda_funcs = []             # 存放所有匿名函数实体

    for ent in entities:
        if ent["category"] == "AnonymousFunction":  # 筛选匿名函数
            lambda_funcs.append(ent)
        id_mapping[ent["id"]] = ent  # 构建 id 索引

    # 按 lambda 限定名（去模块前缀）编号（从 1 开始）
    lambda_func_dict = dict()
    for index, func in enumerate(lambda_funcs):
        lambda_func_dict[func["qualifiedName"].removeprefix(f"{case_dir.name}.")] = index + 1

    return lambda_func_dict


# =============================================================================
# build_pyanalyzer_call：从 PyAnalyzer 的 dep JSON 中提取所有 "Call" 类型关系，
#                        构建调用关系集合（caller, callee），并将 lambda 名称转换
#                        为标准化的 "<lambda序号>" 形式。
# 参数：pyanalyzer_file —— PyAnalyzer dep JSON 路径
#       top_dir         —— 顶层目录（用于去模块前缀）
# 返回：{(caller, callee), ...} 调用关系集合
# =============================================================================
def build_pyanalyzer_call(pyanalyzer_file: Path, top_dir: Path) -> Set[Tuple[str, str]]:

    # 内部辅助函数：将形如 "foo(3)" 的 lambda 名转换为 "foo<lambda3>"
    def translate_lambda_repr(func: str) -> str:
        if prefix := remove_lambda_postfix(func):
            lambda_index = lambda_func_dict[func]          # 查找该 lambda 的编号
            return prefix + f"<lambda{lambda_index}>"      # 拼接标准化形式
        else:
            return func  # 非 lambda 函数，直接返回原名

    dep = json.loads(pyanalyzer_file.read_text())  # 加载 dep JSON
    entities = dep["variables"]    # 所有实体
    relations = dep["cells"]       # 所有关系（调用、定义、继承等）
    id_mapping = dict()            # id → 实体 的映射
    ret = set()                    # 最终结果集：(caller, callee) 元组集合
    lambda_funcs = []              # 匿名函数列表（用于编号）

    # 构建 id 索引，同时收集匿名函数
    for ent in entities:
        if ent["category"] == "AnonymousFunction":
            lambda_funcs.append(ent)
        id_mapping[ent["id"]] = ent

    # 按 lambda 在源文件中的起始行号排序，确保编号顺序稳定
    lambda_funcs.sort(key=lambda e: e["location"]["startLine"])

    # 构建 lambda 名 → 序号 映射
    lambda_func_dict = dict()
    for index, func in enumerate(lambda_funcs):
        lambda_func_dict[func["qualifiedName"].removeprefix(f"{top_dir.name}.")] = index + 1

    # 遍历所有关系，提取 "Call" 类型
    for rel in relations:
        values = rel["values"]   # 关系的附加属性（含 kind、resolved 等）
        kind = values["kind"]    # 关系类型字符串
        from_id = rel["src"]     # 调用者实体 id
        to_id = rel["dest"]      # 被调用者实体 id

        src_ent = id_mapping[from_id]
        if src_ent["File"].endswith("builtins"):
            continue  # 跳过来自内置模块的调用，避免污染结果

        if kind == "Call":
            # 取调用者限定名，去除顶层模块前缀
            caller = id_mapping[from_id]["qualifiedName"]
            callee = id_mapping[to_id]
            callee_qualified_name = callee["qualifiedName"]

            # 添加直接调用关系（调用点静态解析到的目标）
            ret.add((caller.removeprefix(f"{top_dir.name}."),
                     callee_qualified_name.removeprefix(f"{top_dir.name}.")))

            # 若存在动态解析（resolved）的目标，也一并加入
            if "resolved" in values:
                for resolve_id in values["resolved"]:
                    callee = id_mapping[resolve_id]
                    callee_qualified_name = callee["qualifiedName"]
                    if "builtins.__init__" in callee_qualified_name:
                        continue  # 跳过内置 __init__，避免噪声
                    ret.add((caller.removeprefix(f"{top_dir.name}."),
                             callee_qualified_name.removeprefix(f"{top_dir.name}.")))

    # 对结果集中的每条关系，将 lambda 名称转换为标准化形式后返回
    return set(map(lambda rel: (translate_lambda_repr(rel[0]), translate_lambda_repr(rel[1])), ret))


# =============================================================================
# strip_case_name：批量去除调用关系集合中每条关系两端函数名的 "caseName." 前缀。
# 参数：case_name  —— 用例名称（目录名）
#       call_graph —— 原始调用关系集合
# 返回：去掉前缀后的调用关系集合
# =============================================================================
def strip_case_name(case_name: str, call_graph: Set[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    return set(
        map(lambda rel: (rel[0].removeprefix(f"{case_name}."),
                         rel[1].removeprefix(f"{case_name}.")),
            call_graph))


# =============================================================================
# get_call_relation：从 PyCG 格式的 JSON 调用图文件中读取调用关系集合。
#                    若同时提供 pyanalyzer_dep，则额外对 lambda 名称做标准化处理。
# 参数：case_dir       —— 当前测试用例目录
#       pycg_file      —— PyCG 格式的调用图 JSON 路径（含 ground truth）
#       pyanalyzer_dep —— （可选）PyAnalyzer dep JSON，用于 lambda 编号映射
# 返回：{(caller, callee), ...} 调用关系集合
# =============================================================================
def get_call_relation(case_dir: Path, pycg_file: Path, pyanalyzer_dep: Path = None) -> Set[Tuple[str, str]]:

    # 内部辅助函数：将 lambda 名称转换为 "<lambda序号>" 形式
    def translate_lambda_repr(func: str) -> str:
        if prefix := remove_lambda_postfix(func):
            lambda_index = lambda_func_dict[func]
            return prefix + f"<lambda{lambda_index}>"
        else:
            return func

    ret = set()
    dep = json.loads(pycg_file.read_text())  # 加载调用图 JSON

    # PyCG 格式：{caller: [callee1, callee2, ...], ...}
    for key, value in dep.items():
        if pyanalyzer_dep and key.startswith("builtins"):
            continue  # 在与 PyAnalyzer 对比时，跳过内置模块的调用者
        for callee in value:
            if "__init__" in callee:
                continue  # 跳过 __init__ 噪声调用
            ret.add((key, callee
                     .replace("<builtin>", "builtins")        # 统一内置模块前缀
                     .replace("<**PyDict**>", "builtins.dict")  # 统一字典内置类型
                     .replace("<**PyStr**>", "builtins.str")))  # 统一字符串内置类型

    if pyanalyzer_dep:
        # 需要标准化 lambda 名称：先建立 lambda 编号字典
        lambda_func_dict = create_lambda_dict(case_dir, pyanalyzer_dep)
        # 去除用例名前缀后再做 lambda 名转换
        striped_case_prefix = strip_case_name(case_dir.name, ret)
        return set(map(lambda rel: (translate_lambda_repr(rel[0]), translate_lambda_repr(rel[1])),
                       striped_case_prefix))
    else:
        return ret  # 不需要标准化时直接返回


# 类型别名：调用关系集合
CallRelation = Set[Tuple[str, str]]


# =============================================================================
# test_if_same_call_relation：对单个测试用例，同时构建四种调用关系：
#   1. pyanalyzer_call_relation         —— PyAnalyzer 静态分析得到的所有 Call 关系
#   2. pyanalyzer_resolved_call_relation —— PyAnalyzer 调用图（含动态解析）
#   3. pycg_call_relation               —— PyCG 调用图
#   4. ground_truth_call_relation       —— 人工标注的 ground truth 调用图
# 返回：四种调用关系的元组
# =============================================================================
def test_if_same_call_relation(
        case_dir: Path,
        ground_truth_file: Path,
        pyanalyzer_file: Path,
        pyanalyzer_cg: Path,
        pycg_path: Path
) -> Tuple[CallRelation, CallRelation, CallRelation, CallRelation]:

    # 从 dep JSON 中提取 PyAnalyzer 原始 Call 关系（含直接+解析）
    pyanalyzer_call_relation = build_pyanalyzer_call(pyanalyzer_file, case_dir)

    # 从 PyAnalyzer 生成的调用图 JSON 中读取关系（含 lambda 标准化）
    pyanalyzer_resolved_call_relation = get_call_relation(case_dir, pyanalyzer_cg, pyanalyzer_file)

    # 从 ground truth JSON 中读取标准调用关系
    ground_truth_call_relation = get_call_relation(case_dir, ground_truth_file)

    # 从 PyCG 输出的 JSON 中读取调用关系
    pycg_call_relation = get_call_relation(case_dir, pycg_path)

    return (pyanalyzer_call_relation, pyanalyzer_resolved_call_relation,
            pycg_call_relation, ground_truth_call_relation)


# =============================================================================
# CompareItem：存储单个测试用例的对比统计结果（数据类）
# =============================================================================
@dataclass
class CompareItem:
    case: str                                    # 用例相对路径（字符串标识）
    pyanalyzer_and_truth: int                    # PyAnalyzer(all) 与 truth 的交集大小
    pyanalyzer_resolved_and_truth: int           # PyAnalyzer(resolved) 与 truth 的交集大小
    pycg_and_truth: int                          # PyCG 与 truth 的交集大小
    pyanalyzer_count: int                        # PyAnalyzer(all) 的调用关系总数
    pyanalyzer_resolved_count: int               # PyAnalyzer(resolved) 的调用关系总数
    pycg_count: int                              # PyCG 的调用关系总数
    truth_count: int                             # ground truth 的调用关系总数
    pyanalyzer_call_graph: CallRelation          # PyAnalyzer(all) 的完整调用关系集合
    pyanalyzer_call_graph_resolved: CallRelation # PyAnalyzer(resolved) 的完整调用关系集合
    pycg_call_graph: CallRelation               # PyCG 的完整调用关系集合
    truth: CallRelation                          # ground truth 的完整调用关系集合


# =============================================================================
# dump_call_graph_compare：将多个 CompareItem 写出为 CSV 文件。
# 参数：out_name —— 输出 CSV 文件名
#       rows     —— CompareItem 的可迭代对象
# =============================================================================
def dump_call_graph_compare(out_name: str, rows: Iterable[CompareItem]) -> None:
    with open(out_name, "w", newline="") as file:
        writer = csv.writer(file)

        # 写表头行
        writer.writerow([
            "case",
            "pyanalyzer(all)&truth",           # PyAnalyzer 全量与 truth 的交集数
            "pyanalyzer(all)",                 # PyAnalyzer 全量调用关系总数
            "pyanalyzer(resolved) & truth",    # PyAnalyzer 解析后与 truth 的交集数
            "pyanalyzer(resolved)",            # PyAnalyzer 解析后调用关系总数
            "pycg & truth",                    # PyCG 与 truth 的交集数
            "pycg",                            # PyCG 调用关系总数
            "truth",                           # ground truth 调用关系总数
            "pyanalyzer data(all)",            # PyAnalyzer 全量调用关系集合（原始数据）
            "pyanalyzer data(resolved)",       # PyAnalyzer 解析后调用关系集合（原始数据）
            "truth data",                      # ground truth 调用关系集合（原始数据）
            "pyanalyzer(resolved) - truth",    # PyAnalyzer 多报（假阳性）
            "truth - pyanalyzer(resolved)",    # PyAnalyzer 漏报（假阴性）
            "pycg - truth",                    # PyCG 多报（假阳性）
            "truth - pycg"                     # PyCG 漏报（假阴性）
        ])

        # 写数据行：每个 CompareItem 对应一行
        writer.writerows(
            (
                row.case,
                row.pyanalyzer_and_truth,
                row.pyanalyzer_count,
                row.pyanalyzer_resolved_and_truth,
                row.pyanalyzer_resolved_count,
                row.pycg_and_truth,
                row.pycg_count,
                row.truth_count,
                row.pyanalyzer_call_graph,
                row.pyanalyzer_call_graph_resolved,
                row.truth,
                row.pyanalyzer_call_graph_resolved.difference(row.truth),   # 假阳性
                row.truth.difference(row.pyanalyzer_call_graph_resolved),   # 假阴性
                row.pycg_call_graph.difference(row.truth),                  # PyCG 假阳性
                row.truth.difference(row.pycg_call_graph)                   # PyCG 假阴性
            )
            for row in rows)


# =============================================================================
# create_compare_item：根据四种调用关系构造 CompareItem 统计对象。
# 参数：case_dir      —— 用例路径字符串
#       pyanalyzer    —— PyAnalyzer(all) 调用关系集合
#       pyanalyzer_cg —— PyAnalyzer(resolved) 调用关系集合
#       pycg_cg       —— PyCG 调用关系集合
#       truth         —— ground truth 调用关系集合
# 返回：CompareItem
# =============================================================================
def create_compare_item(
        case_dir: str,
        pyanalyzer: CallRelation,
        pyanalyzer_cg: CallRelation,
        pycg_cg: CallRelation,
        truth: CallRelation
) -> CompareItem:
    pyanalyzer_and_truth = pyanalyzer.intersection(truth)          # 交集：PyAnalyzer(all) ∩ truth
    pyanalyzer_resolved_and_truth = pyanalyzer_cg.intersection(truth)  # 交集：PyAnalyzer(resolved) ∩ truth
    pycg_and_truth = pycg_cg.intersection(truth)                   # 交集：PyCG ∩ truth

    return CompareItem(
        case=case_dir,
        pyanalyzer_resolved_and_truth=len(pyanalyzer_resolved_and_truth),
        pyanalyzer_and_truth=len(pyanalyzer_and_truth),
        pycg_and_truth=len(pycg_and_truth),
        pyanalyzer_resolved_count=len(pyanalyzer_cg),
        pyanalyzer_count=len(pyanalyzer),
        pycg_count=len(pycg_cg),
        truth_count=len(truth),
        pyanalyzer_call_graph=pyanalyzer,
        pyanalyzer_call_graph_resolved=pyanalyzer_cg,
        pycg_call_graph=pycg_cg,
        truth=truth
    )


# =============================================================================
# gen_precision_for_all：遍历 benchmark 目录下的所有测试用例，
#                        批量计算精确度并输出两份 CSV：
#                          1. {prefix}-precision.csv       —— 全量对比结果
#                          2. {prefix}-unresolved_cases.csv —— PyAnalyzer 未能完整覆盖的用例
# 参数：snippets —— benchmark 根目录（下含多个场景子目录，每个子目录含多个用例）
#       prefix   —— 输出文件名前缀
# =============================================================================
def gen_precision_for_all(snippets: Path, prefix: str):
    rows: List[CompareItem] = []  # 汇总所有用例的对比结果

    # 遍历 benchmark 中的每个场景子目录（如 args、calls、dicts 等）
    for sub_dir in snippets.iterdir():
        # 遍历每个具体测试用例目录
        for case in sub_dir.iterdir():
            if not case.is_dir():
                continue  # 跳过散落文件

            # 构造各文件路径
            pyanalyzer_path = sub_dir.joinpath(f"{case.name}-report-pyanalyzer.json")         # PyAnalyzer dep JSON
            pyanalyzer_resolved_call_graph_path = sub_dir.joinpath(f"{case.name}-call-graph-pyanalyzer.json")  # PyAnalyzer 调用图 JSON
            ground_truth_path = case.joinpath("callgraph.json")    # ground truth 调用图
            pycg_path = case.joinpath(f"pycg-callgraph.json")       # PyCG 调用图

            # 计算四种调用关系
            pyanalyzer, pyanalyzer_cg, pycg_cg, ground_truth_cg = test_if_same_call_relation(
                case, ground_truth_path, pyanalyzer_path,
                pyanalyzer_resolved_call_graph_path, pycg_path)

            # 构造对比统计对象并加入结果列表
            rows.append(create_compare_item(
                str(case.relative_to(snippets)),  # 相对路径作为用例标识
                pyanalyzer, pyanalyzer_cg, pycg_cg, ground_truth_cg))

    # 输出全量对比 CSV
    dump_call_graph_compare(f"{prefix}-precision.csv", rows)

    # 筛选出 PyAnalyzer(resolved) 未能完整覆盖 truth 的用例（漏报用例）
    unresolved_rows = (r for r in rows if r.pyanalyzer_resolved_and_truth != r.truth_count)
    dump_call_graph_compare(f"{prefix}-unresolved_cases.csv", unresolved_rows)


# =============================================================================
# entry：程序入口函数。
#        定义 benchmark 路径，依次运行 PyAnalyzer + PyCG，最后生成对比报告。
# =============================================================================
def entry():
    # 两组 benchmark 路径
    pycg_snippets = Path("Benchmark-collectedbyPyCG")          # PyCG 收集的 benchmark
    pyanalyzer_snippets = Path("Benchmark-newlyaddedbyPyAnalyzer")  # PyAnalyzer 新增的 benchmark

    pyanalyzer_case_dir = pyanalyzer_snippets.absolute()  # 转为绝对路径
    pycg_case_dir = pycg_snippets.absolute()

    # 对 PyAnalyzer benchmark 运行工具（生成各用例的调用图文件）
    align_test_dir(pyanalyzer_case_dir)

    # 切回根目录，避免后续相对路径出错
    os.chdir(pyanalyzer_case_dir.parent)

    # 生成 PyCG benchmark 的精确度对比报告
    gen_precision_for_all(pycg_case_dir, "pycg-benchmark")


if __name__ == "__main__":
    entry()