# =============================================================================
# pycg_Script.py
# 功能：与 S3_call_dependency.py 功能重叠，专门针对 PyCG benchmark 和
#       PyAnalyzer benchmark 批量调用外部工具生成调用图，并将两者结果与
#       ground truth 对比，输出 CSV 精确度报告。
#
# 注：本文件与 S3_call_dependency.py 的主体逻辑完全相同，差异仅在于
#     entry() 函数中的调用顺序和注释掉的代码段。
# =============================================================================

import csv                          # 写 CSV 格式的对比报告
import json                         # 解析 JSON 文件（dep、调用图等）
import os                           # 操作系统接口（切换目录等）
import subprocess                   # 调用外部可执行文件（PyAnalyzer、PyCG）
from dataclasses import dataclass   # 数据类装饰器
from pathlib import Path            # 面向对象的路径操作
from typing import Tuple, Dict, List, Set, Iterable, Optional  # 类型注解


# =============================================================================
# align_test_dir：遍历指定 benchmark 目录，对每个测试用例分别调用：
#   1. PyAnalyzer（__main__.exe）生成控制流图和调用图
#   2. PyCG 生成调用图，并清理输出中的路径前缀
# 参数：dir —— benchmark 顶层目录路径
# =============================================================================
def align_test_dir(dir: Path):
    # 遍历顶层目录下的每个场景子目录（如 args、calls 等）
    for sub_dir in dir.absolute().iterdir():
        os.chdir(sub_dir)  # 切换到场景子目录

        # 遍历场景子目录下的每个具体测试用例
        for case in sub_dir.iterdir():
            case_name = case.name  # 记录用例目录名，用于后续前缀替换

            if case.is_dir():  # 只处理目录，跳过普通文件

                # 调用 PyAnalyzer 可执行文件：
                #   {case}          —— 待分析的用例目录
                #   --cfg           —— 生成控制流图（Control Flow Graph）
                #   --cg            —— 生成调用图（Call Graph）
                #   --builtins ...  —— 指定内置函数描述文件目录路径
                subprocess.call(
                    f"__main__.exe {case} --cfg --cg --builtins F:\\Master\\Python\\AIDep-py\\test\\builtins",
                    shell=True)

                # 调用 PyCG 对 main.py 进行调用图分析，输出到 pycg-callgraph.json
                subprocess.call(
                    "pycg {} -o {}"
                    .format(case.joinpath("main.py"), case.joinpath("pycg-callgraph.json")),
                    shell=True)

                # 清理 PyCG 输出文件中的路径前缀：
                # PyCG 默认将函数名写为 "caseName\\funcName"，此处替换去掉前缀，
                # 使其格式与 PyAnalyzer 输出对齐，便于后续集合比较。
                case.joinpath("pycg-callgraph.json") \
                    .write_text(case.joinpath("pycg-callgraph.json")
                                .read_text().replace(f"{case_name}\\\\", ""))


import re  # 正则表达式，用于匹配 lambda 函数名后缀

# 正则模式：匹配末尾带 "(数字)" 的函数名（PyAnalyzer 对 lambda 的命名格式）
lambda_pattern = "(.*)\\(\d+\\)$"
compiled_pattern = re.compile(lambda_pattern)  # 预编译提升性能

# 以下三个变量为类型别名（均为 int），仅作代码文档说明用途
pyanalyzerCall = int   # PyAnalyzer 识别到的调用关系数量
PycgCall = int         # PyCG 识别到的调用关系数量
SameCall = int         # 两者共同识别到的调用关系数量


# =============================================================================
# remove_lambda_postfix：检测并去除函数名末尾的 "(数字)" lambda 后缀。
# 参数：longname —— 可能含后缀的函数限定名（如 "module.func(3)"）
# 返回：去掉后缀的前缀（如 "module.func"），若无后缀则返回 None
# =============================================================================
def remove_lambda_postfix(longname: str) -> Optional[str]:
    if matched := re.match(compiled_pattern, longname):
        removed_postfix = matched.group(1)  # 第 1 捕获组：括号前的部分
        return removed_postfix
    return None  # 不匹配，说明不是 lambda 函数名格式


# =============================================================================
# create_lambda_dict：从 PyAnalyzer dep JSON 中提取所有匿名函数，
#                     按定义顺序编号，构建 {限定名: 序号} 字典，
#                     用于后续将 "func(3)" 格式转换为 "func<lambda3>"。
# 参数：case_dir        —— 用例目录（用于去除模块名前缀）
#       pyanalyzer_file —— PyAnalyzer dep JSON 路径
# 返回：{lambda限定名（去前缀）: 序号} 字典（序号从 1 开始）
# =============================================================================
def create_lambda_dict(case_dir: Path, pyanalyzer_file: Path) -> Dict[str, int]:
    dep = json.loads(pyanalyzer_file.read_text())  # 加载 dep JSON 文件
    entities = dep["variables"]   # 所有代码实体
    id_mapping = dict()           # {id: 实体对象} 映射，便于通过 id 查找实体
    lambda_funcs = []             # 收集所有匿名函数实体

    for ent in entities:
        if ent["category"] == "AnonymousFunction":  # 仅提取匿名函数
            lambda_funcs.append(ent)
        id_mapping[ent["id"]] = ent  # 构建 id 索引

    # 按出现顺序对 lambda 编号（1-based）
    lambda_func_dict = dict()
    for index, func in enumerate(lambda_funcs):
        # 去掉 "caseName." 前缀后作为 key
        lambda_func_dict[func["qualifiedName"].removeprefix(f"{case_dir.name}.")] = index + 1

    return lambda_func_dict


# =============================================================================
# build_pyanalyzer_call：从 PyAnalyzer dep JSON 中提取所有 Call 关系，
#                        同时包含静态调用目标和动态解析（resolved）目标，
#                        并将 lambda 名称标准化为 "<lambda序号>" 形式。
# 参数：pyanalyzer_file —— PyAnalyzer dep JSON 路径
#       top_dir         —— benchmark 根目录（用于去除模块前缀）
# 返回：{(caller, callee), ...} 调用关系集合
# =============================================================================
def build_pyanalyzer_call(pyanalyzer_file: Path, top_dir: Path) -> Set[Tuple[str, str]]:

    # 内部辅助：将 "foo(3)" 形式的 lambda 名转换为 "foo<lambda3>"
    def translate_lambda_repr(func: str) -> str:
        if prefix := remove_lambda_postfix(func):
            lambda_index = lambda_func_dict[func]       # 查找编号
            return prefix + f"<lambda{lambda_index}>"   # 拼接标准化名称
        else:
            return func  # 非 lambda，直接返回

    dep = json.loads(pyanalyzer_file.read_text())  # 加载 dep JSON
    entities = dep["variables"]   # 所有实体
    relations = dep["cells"]      # 所有关系（Call、Define、Inherit 等）
    id_mapping = dict()           # id → 实体 映射
    ret = set()                   # 结果：(caller, callee) 集合
    lambda_funcs = []             # 匿名函数列表

    # 构建 id 索引，收集匿名函数
    for ent in entities:
        if ent["category"] == "AnonymousFunction":
            lambda_funcs.append(ent)
        id_mapping[ent["id"]] = ent

    # 按源码行号排序，保证编号与源码中出现顺序一致
    lambda_funcs.sort(key=lambda e: e["location"]["startLine"])

    # 构建 lambda 名 → 编号 映射（去模块前缀后作 key）
    lambda_func_dict = dict()
    for index, func in enumerate(lambda_funcs):
        lambda_func_dict[func["qualifiedName"].removeprefix(f"{top_dir.name}.")] = index + 1

    # 遍历所有关系，仅处理 kind == "Call" 的条目
    for rel in relations:
        values = rel["values"]   # 关系附加属性
        kind = values["kind"]    # 关系类型
        from_id = rel["src"]     # 调用者 id
        to_id = rel["dest"]      # 被调用者 id（静态目标）

        src_ent = id_mapping[from_id]
        if src_ent["File"].endswith("builtins"):
            continue  # 跳过来自 builtins 文件的关系，避免噪声

        if kind == "Call":
            caller = id_mapping[from_id]["qualifiedName"]
            callee = id_mapping[to_id]
            callee_qualified_name = callee["qualifiedName"]

            # 添加静态直接调用关系（去模块前缀）
            ret.add((caller.removeprefix(f"{top_dir.name}."),
                     callee_qualified_name.removeprefix(f"{top_dir.name}.")))

            # 若存在动态解析目标（如通过类型推断得到的实际调用目标），也加入
            if "resolved" in values:
                for resolve_id in values["resolved"]:
                    callee = id_mapping[resolve_id]
                    callee_qualified_name = callee["qualifiedName"]
                    if "builtins.__init__" in callee_qualified_name:
                        continue  # 过滤内置 __init__ 噪声
                    ret.add((caller.removeprefix(f"{top_dir.name}."),
                             callee_qualified_name.removeprefix(f"{top_dir.name}.")))

    # 对所有结果做 lambda 名称标准化后返回
    return set(map(lambda rel: (translate_lambda_repr(rel[0]), translate_lambda_repr(rel[1])), ret))


# =============================================================================
# strip_case_name：批量去除调用关系集合中函数名的 "caseName." 前缀。
# =============================================================================
def strip_case_name(case_name: str, call_graph: Set[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    return set(
        map(lambda rel: (rel[0].removeprefix(f"{case_name}."),
                         rel[1].removeprefix(f"{case_name}.")),
            call_graph))


# =============================================================================
# get_call_relation：从 PyCG 格式的调用图 JSON 中读取调用关系集合，
#                    并可选地对 lambda 名称做标准化处理。
# 参数：case_dir       —— 用例目录
#       pycg_file      —— PyCG 调用图 JSON 路径（key: caller, value: [callee,...]）
#       pyanalyzer_dep —— （可选）PyAnalyzer dep JSON，用于 lambda 编号映射
# 返回：{(caller, callee), ...}
# =============================================================================
def get_call_relation(case_dir: Path, pycg_file: Path, pyanalyzer_dep: Path = None) -> Set[Tuple[str, str]]:

    def translate_lambda_repr(func: str) -> str:
        if prefix := remove_lambda_postfix(func):
            lambda_index = lambda_func_dict[func]
            return prefix + f"<lambda{lambda_index}>"
        else:
            return func

    ret = set()
    dep = json.loads(pycg_file.read_text())  # 加载 PyCG 格式 JSON

    for key, value in dep.items():
        if pyanalyzer_dep and key.startswith("builtins"):
            continue  # 与 PyAnalyzer 对比时过滤内置调用者
        for callee in value:
            if "__init__" in callee:
                continue  # 过滤 __init__ 噪声
            ret.add((key, callee
                     .replace("<builtin>", "builtins")          # 统一内置前缀
                     .replace("<**PyDict**>", "builtins.dict")  # 统一 dict 类型
                     .replace("<**PyStr**>", "builtins.str")))  # 统一 str 类型

    if pyanalyzer_dep:
        # 需要 lambda 标准化：先建编号字典，再去前缀，再转换
        lambda_func_dict = create_lambda_dict(case_dir, pyanalyzer_dep)
        striped_case_prefix = strip_case_name(case_dir.name, ret)
        return set(map(lambda rel: (translate_lambda_repr(rel[0]), translate_lambda_repr(rel[1])),
                       striped_case_prefix))
    else:
        return ret  # 无需标准化，直接返回


# 类型别名
CallRelation = Set[Tuple[str, str]]


# =============================================================================
# test_if_same_call_relation：对单个用例同时构建四种调用关系集合，返回元组。
# =============================================================================
def test_if_same_call_relation(
        case_dir: Path,
        ground_truth_file: Path,
        pyanalyzer_file: Path,
        pyanalyzer_cg: Path,
        pycg_path: Path
) -> Tuple[CallRelation, CallRelation, CallRelation, CallRelation]:

    pyanalyzer_call_relation = build_pyanalyzer_call(pyanalyzer_file, case_dir)         # PyAnalyzer 原始 Call 集合
    pyanalyzer_resolved_call_relation = get_call_relation(case_dir, pyanalyzer_cg, pyanalyzer_file)  # PyAnalyzer 解析后调用图
    ground_truth_call_relation = get_call_relation(case_dir, ground_truth_file)          # ground truth
    pycg_call_relation = get_call_relation(case_dir, pycg_path)                          # PyCG 调用图

    return (pyanalyzer_call_relation, pyanalyzer_resolved_call_relation,
            pycg_call_relation, ground_truth_call_relation)


# =============================================================================
# CompareItem：单个测试用例的对比结果数据类。
# =============================================================================
@dataclass
class CompareItem:
    case: str                                     # 用例路径标识
    pyanalyzer_and_truth: int                     # PyAnalyzer(all) ∩ truth 的大小
    pyanalyzer_resolved_and_truth: int            # PyAnalyzer(resolved) ∩ truth 的大小
    pycg_and_truth: int                           # PyCG ∩ truth 的大小
    pyanalyzer_count: int                         # PyAnalyzer(all) 调用关系总数
    pyanalyzer_resolved_count: int                # PyAnalyzer(resolved) 调用关系总数
    pycg_count: int                               # PyCG 调用关系总数
    truth_count: int                              # ground truth 调用关系总数
    pyanalyzer_call_graph: CallRelation           # PyAnalyzer(all) 完整集合
    pyanalyzer_call_graph_resolved: CallRelation  # PyAnalyzer(resolved) 完整集合
    pycg_call_graph: CallRelation                 # PyCG 完整集合
    truth: CallRelation                           # ground truth 完整集合


# =============================================================================
# dump_call_graph_compare：将 CompareItem 列表写出为 CSV 对比报告。
# =============================================================================
def dump_call_graph_compare(out_name: str, rows: Iterable[CompareItem]) -> None:
    with open(out_name, "w", newline="") as file:
        writer = csv.writer(file)
        # 表头
        writer.writerow([
            "case",
            "pyanalyzer(all)&truth",            # 交集数：全量
            "pyanalyzer(all)",                  # 全量总数
            "pyanalyzer(resolved) & truth",     # 交集数：解析后
            "pyanalyzer(resolved)",             # 解析后总数
            "pycg & truth",                     # PyCG 交集数
            "pycg",                             # PyCG 总数
            "truth",                            # ground truth 总数
            "pyanalyzer data(all)",             # 全量集合原始数据
            "pyanalyzer data(resolved)",        # 解析后集合原始数据
            "truth data",                       # ground truth 原始数据
            "pyanalyzer(resolved) - truth",     # 假阳性（多报）
            "truth - pyanalyzer(resolved)",     # 假阴性（漏报）
            "pycg - truth",                     # PyCG 假阳性
            "truth - pycg"                      # PyCG 假阴性
        ])
        # 数据行
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
                row.pyanalyzer_call_graph_resolved.difference(row.truth),  # 假阳性
                row.truth.difference(row.pyanalyzer_call_graph_resolved),  # 假阴性
                row.pycg_call_graph.difference(row.truth),                 # PyCG 假阳性
                row.truth.difference(row.pycg_call_graph)                  # PyCG 假阴性
            )
            for row in rows)


# =============================================================================
# create_compare_item：根据四种调用关系构造 CompareItem 统计对象。
# =============================================================================
def create_compare_item(
        case_dir: str,
        pyanalyzer: CallRelation,
        pyanalyzer_cg: CallRelation,
        pycg_cg: CallRelation,
        truth: CallRelation
) -> CompareItem:
    pyanalyzer_and_truth = pyanalyzer.intersection(truth)              # 全量 ∩ truth
    pyanalyzer_resolved_and_truth = pyanalyzer_cg.intersection(truth)  # 解析后 ∩ truth
    pycg_and_truth = pycg_cg.intersection(truth)                       # PyCG ∩ truth

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
# gen_precision_for_all：批量处理 benchmark 目录，生成两份 CSV 报告：
#   1. {prefix}-precision.csv       —— 全量对比结果
#   2. {prefix}-unresolved_cases.csv —— 存在漏报的用例
# =============================================================================
def gen_precision_for_all(snippets: Path, prefix: str):
    rows: List[CompareItem] = []  # 汇总结果

    for sub_dir in snippets.iterdir():        # 遍历场景子目录
        for case in sub_dir.iterdir():        # 遍历用例目录
            if not case.is_dir():
                continue

            # 各输入文件路径
            pyanalyzer_path = sub_dir.joinpath(f"{case.name}-report-pyanalyzer.json")
            pyanalyzer_resolved_call_graph_path = sub_dir.joinpath(f"{case.name}-call-graph-pyanalyzer.json")
            ground_truth_path = case.joinpath("callgraph.json")
            pycg_path = case.joinpath(f"pycg-callgraph.json")

            # 计算四种调用关系
            pyanalyzer, pyanalyzer_cg, pycg_cg, ground_truth_cg = test_if_same_call_relation(
                case, ground_truth_path, pyanalyzer_path,
                pyanalyzer_resolved_call_graph_path, pycg_path)

            rows.append(create_compare_item(
                str(case.relative_to(snippets)),
                pyanalyzer, pyanalyzer_cg, pycg_cg, ground_truth_cg))

    # 输出全量报告
    dump_call_graph_compare(f"{prefix}-precision.csv", rows)

    # 筛选漏报用例（PyAnalyzer resolved 未完整覆盖 truth）
    unresolved_rows = (r for r in rows if r.pyanalyzer_resolved_and_truth != r.truth_count)
    dump_call_graph_compare(f"{prefix}-unresolved_cases.csv", unresolved_rows)


# =============================================================================
# entry：程序入口。
#        注意：与 S3_call_dependency.py 相比，本文件的差异在于：
#          - align_test_dir 仅对 pyanalyzer_case_dir 调用（PyCG benchmark 的对齐被注释）
#          - gen_precision_for_all 只生成 pycg-benchmark 报告
# =============================================================================
def entry():
    pycg_snippets = Path("Benchmark-collectedbyPyCG")              # PyCG benchmark 路径
    pyanalyzer_snippets = Path("Benchmark-newlyaddedbyPyAnalyzer")  # PyAnalyzer benchmark 路径

    pyanalyzer_case_dir = pyanalyzer_snippets.absolute()
    pycg_case_dir = pycg_snippets.absolute()

    # 对 PyAnalyzer benchmark 目录运行工具，生成调用图文件
    align_test_dir(pyanalyzer_case_dir)

    # 切回父目录，防止后续路径解析错误
    os.chdir(pyanalyzer_case_dir.parent)

    # 仅生成 PyCG benchmark 的精确度对比报告
    gen_precision_for_all(pycg_case_dir, "pycg-benchmark")


if __name__ == "__main__":
    entry()