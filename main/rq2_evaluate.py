"""
rq2_evaluate.py
功能：复现论文 Table 7 中 PyAnalyzer 调用图（Call Graph）的 P/R/F1 评估结果。
      对 Micro-Benchmark B 的每个测试用例，将 PyAnalyzer 的调用图输出与
      ground truth callgraph.json 进行集合比较，按类别统计后输出汇总表格。

目录结构要求（与原始仓库一致）:
  ICSE2024_PyAnalyzer-main/
  ├── rq2_evaluate.py              ← 本脚本
  └── Data/RQ2/
      ├── micro-benchmark B/
      │   ├── Benchmark-collectedbyPyCG/
      │   │   └── {category}/{case}/callgraph.json   ← GT
      │   └── Benchmark-newlyaddedbyPyAnalyzer/
      │       └── {category}/{case}/callgraph.json   ← GT
      └── results/PyAnalyzer/
          ├── Benchmark-collectedbyPyCG/
          │   └── {category}/{case}-call-graph-PyAnalyzer.json  ← 预测
          └── Benchmark-newlyaddedbyPyAnalyzer/
              └── {category}/{case}-call-graph-PyAnalyzer.json  ← 预测

论文参考值（Table 7 PyAnalyzer）:
  TOTAL:  P=92.9%  R=96.1%  F1=94.5%

运行:
  python rq2_evaluate.py              # 默认路径，输出 Table 7 格式
  python rq2_evaluate.py -v           # 显示每个 case 的详细结果
  python rq2_evaluate.py --csv out.csv  # 同时输出 CSV 文件
"""

import os, re, json, glob, argparse, csv
from pathlib import Path
from typing import Set, Tuple, Dict, List, Optional

# 脚本所在目录（绝对路径），作为默认路径的基准
BASE = os.path.dirname(os.path.abspath(__file__))

# 调用关系类型别名：(调用者限定名, 被调用者限定名) 的集合
CallSet = Set[Tuple[str, str]]


# =============================================================================
# Lambda 名称翻译
# 与 pycg_Script.py 中的逻辑完全一致，确保评估结果可与原始脚本对齐
# =============================================================================
# 正则：匹配 PyAnalyzer 对 lambda 函数的命名格式，如 "outer.func(3)"
_LAMBDA_RE = re.compile(r"(.*)\((\d+)\)$")


def _remove_lambda_postfix(name: str) -> Optional[str]:
    """
    若 name 末尾带有 "(数字)" 形式的 lambda 后缀，返回去掉后缀的前缀；
    否则返回 None（表示不是 lambda 名称格式）。
    例：'outer.func(3)' → 'outer.func'
    """
    m = _LAMBDA_RE.match(name)
    return m.group(1) if m else None


def _build_lambda_dict(report_json: dict, case_name: str) -> Dict[str, int]:
    """
    从 PyAnalyzer 的 report JSON（dep JSON 格式）中提取所有匿名函数实体，
    按源码出现的行号升序排序后，从 1 开始编号，构建名称→序号的映射字典。

    参数：
      report_json —— PyAnalyzer 报告 JSON（含 'variables' 字段）
      case_name   —— 用例名称，用于去掉限定名中的顶层包前缀

    返回：
      {去掉顶层包前缀的限定名: 序号（1-based）}
    例：{'outer.func(3)': 1, 'inner.bar(7)': 2}
    """
    # 筛选出所有匿名函数（lambda）实体
    anon = [v for v in report_json.get('variables', [])
            if v.get('category') == 'AnonymousFunction']
    # 按起始行号升序排序，确保编号与源码顺序一致
    anon.sort(key=lambda e: e.get('location', {}).get('startLine', 0))

    prefix = f"{case_name}."
    res = {}
    for idx, v in enumerate(anon):
        name = v['qualifiedName']
        # 去掉顶层包前缀（如 "assigned_call."）
        if name.startswith(prefix):
            name = name[len(prefix):]
        res[name] = idx + 1  # 序号从 1 开始
    return res


def _translate_lambda(name: str, lambda_dict: Dict[str, int]) -> str:
    """
    将 PyAnalyzer 格式的 lambda 名称（如 "func(3)"）转换为标准化格式（如 "func<lambda3>"）。
    若 name 不是 lambda 格式，或序号在字典中找不到，则直接返回原名。

    参数：
      name        —— 待转换的函数名
      lambda_dict —— 由 _build_lambda_dict 生成的序号映射
    """
    prefix = _remove_lambda_postfix(name)
    if prefix is not None:
        idx = lambda_dict.get(name)
        if idx is not None:
            return prefix + f"<lambda{idx}>"  # 拼接为标准化格式
    return name  # 非 lambda 或序号未知时直接返回


# =============================================================================
# 加载 PyAnalyzer 预测调用图
# =============================================================================
def load_pyanalyzer_callgraph(cg_path: str, report_path: str,
                               case_name: str) -> CallSet:
    """
    读取 PyAnalyzer 输出的 {case}-call-graph-PyAnalyzer.json 文件，
    经过以下处理后返回标准化调用关系集合：

    处理步骤：
      1. 过滤以 "builtins" 开头的 caller（内置模块调用，不在比较范围内）
      2. 过滤含 "__init__" 的 callee（初始化方法调用，GT 中也不包含）
      3. 统一特殊符号的表示（<builtin>→builtins、<**PyDict**>→builtins.dict 等）
      4. 去掉 case_name 前缀（如 "assigned_call."）使限定名相对化
      5. 将 lambda 名称转换为标准化格式（利用 report 文件中的序号信息）

    该处理流程与 pycg_Script.py::get_call_relation(pyanalyzer_dep 非空) 完全一致。

    参数：
      cg_path     —— 调用图 JSON 文件路径
      report_path —— 对应的 report JSON 路径（用于 lambda 序号映射，可选）
      case_name   —— 用例名称（用于去前缀）

    返回：{(caller, callee), ...} 调用关系集合
    """
    with open(cg_path, encoding='utf-8') as f:
        cg = json.load(f)  # 格式：{caller: [callee1, callee2, ...]}

    # 尝试加载 report 文件，用于 lambda 序号映射
    lambda_dict: Dict[str, int] = {}
    if report_path and os.path.exists(report_path):
        with open(report_path, encoding='utf-8') as f:
            report = json.load(f)
        lambda_dict = _build_lambda_dict(report, case_name)

    raw: CallSet = set()
    for caller, callees in cg.items():
        # 过滤来自内置模块的 caller
        if caller.startswith("builtins"):
            continue
        for callee in callees:
            # 过滤 __init__ 方法调用（GT 中不包含此类条目）
            if "__init__" in callee:
                continue
            # 统一特殊表示符号，与 GT 格式对齐
            raw.add((caller, callee
                     .replace("<builtin>", "builtins")
                     .replace("<**PyDict**>", "builtins.dict")
                     .replace("<**PyStr**>", "builtins.str")))

    # ── 去掉 case_name 前缀（相对化）──
    prefix = f"{case_name}."
    stripped: CallSet = set()
    for c, e in raw:
        new_c = c[len(prefix):] if c.startswith(prefix) else c
        new_e = e[len(prefix):] if e.startswith(prefix) else e
        stripped.add((new_c, new_e))

    # ── 翻译 lambda 名称为标准格式 ──
    return {
        (_translate_lambda(c, lambda_dict), _translate_lambda(e, lambda_dict))
        for c, e in stripped
    }


# =============================================================================
# 加载 Ground Truth 调用图
# =============================================================================
def load_gt_callgraph(gt_path: str) -> CallSet:
    """
    读取 PyCG benchmark 格式的 callgraph.json 文件，
    提取调用关系集合并做与预测相同的规范化处理。

    GT 文件格式：{"caller": ["callee1", "callee2", ...]}

    处理步骤：
      - 过滤含 "__init__" 的 callee（与预测过滤保持一致）
      - 统一特殊符号表示（<builtin>→builtins 等）

    注意：GT 文件中函数名已经是去掉 case 前缀的相对形式，无需再处理前缀。
    """
    with open(gt_path, encoding='utf-8') as f:
        data = json.load(f)
    result: CallSet = set()
    for caller, callees in data.items():
        for callee in callees:
            if "__init__" in callee:
                continue  # 与预测加载保持一致，过滤初始化方法
            result.add((caller, callee
                         .replace("<builtin>", "builtins")
                         .replace("<**PyDict**>", "builtins.dict")
                         .replace("<**PyStr**>", "builtins.str")))
    return result


# =============================================================================
# P / R / F1 计算
# =============================================================================
def prf1(pred: CallSet, gt: CallSet) -> Tuple[float, float, float]:
    """
    基于精确集合匹配计算 Precision、Recall 和 F1。

    计算公式：
      TP = |pred ∩ gt|
      P  = TP / |pred|   （若 pred 为空则 P=0）
      R  = TP / |gt|     （若 gt 为空则 R=0）
      F1 = 2*P*R/(P+R)   （若 P+R=0 则 F1=0）

    特殊情况：pred 和 gt 均为空集时，视为完美预测，返回 (1.0, 1.0, 1.0)。
    """
    if not pred and not gt:
        return 1.0, 1.0, 1.0
    tp = len(pred & gt)                          # 真正例数（精确匹配）
    p  = tp / len(pred) if pred else 0.0         # 精确率
    r  = tp / len(gt)   if gt   else 0.0         # 召回率
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0  # F1 分数
    return p, r, f1


# =============================================================================
# 主评估流程
# =============================================================================
def evaluate(results_root: str, benchmark_root: str,
             verbose: bool = False, csv_path: str = None):
    """
    遍历 PyAnalyzer 在 micro-benchmark B 上的所有预测结果，
    按测试类别（category）汇总后计算全局 P/R/F1，输出论文 Table 7 格式的结果表。

    评估策略：
      - 对每个类别，将该类别所有用例的 pred 和 GT 合并为池（pool），
        再对池整体计算一次 P/R/F1（微平均，与论文一致）
      - 最后对所有类别的池再做一次全局汇总

    参数：
      results_root  —— PyAnalyzer 结果根目录（下含两个 benchmark 子目录）
      benchmark_root—— micro-benchmark B 根目录（含 callgraph.json GT 文件）
      verbose       —— 是否打印每个用例的详细 FP/FN
      csv_path      —— 若指定则同时将结果写入 CSV 文件
    """
    # category → {pred_pool: set, gt_pool: set, cases: list}
    category_stats: Dict[str, Dict] = {}

    csv_rows: List[dict] = []   # 用于输出 CSV 的行数据
    skipped:  List[str]  = []   # 跳过的用例列表（含原因）

    # ── 遍历两个 benchmark 子目录 ──
    for bench_name in ['Benchmark-collectedbyPyCG', 'Benchmark-newlyaddedbyPyAnalyzer']:
        res_bench = os.path.join(results_root, bench_name)   # 预测结果子目录
        gt_bench  = os.path.join(benchmark_root, bench_name) # GT 子目录

        if not os.path.isdir(res_bench):
            continue  # 该 benchmark 子目录不存在：跳过

        # 递归搜索所有预测调用图文件
        pattern = os.path.join(res_bench, '**', '*-call-graph-PyAnalyzer.json')
        files = sorted(glob.glob(pattern, recursive=True))
        if not files:
            continue

        for cg_file in files:
            cg_path = Path(cg_file)

            # ── 解析目录结构，提取 category 和 case_name ──
            # 兼容两种目录结构：
            #   结构1: {bench}/{category}/{case}/{case}-call-graph-PyAnalyzer.json（最常见）
            #   结构2: {bench}/{category}/{case}-call-graph-PyAnalyzer.json（少数新增 case）
            try:
                rel_path = Path(os.path.relpath(cg_file, res_bench))
            except Exception:
                continue

            parts = rel_path.parts
            if len(parts) >= 3:
                # 结构1：category/case/xxx.json → parts[0]=category, parts[1]=case
                category  = parts[0]
                case_name = parts[1]
            elif len(parts) == 2:
                # 结构2：category/{case}-call-graph-PyAnalyzer.json
                category  = parts[0]
                case_name = parts[1].replace('-call-graph-PyAnalyzer.json', '')
            else:
                continue  # 目录层级不足：跳过

            # ── 定位对应的 report 文件（用于 lambda 序号映射）──
            report_path = str(cg_path.parent / f"{case_name}-report-PyAnalyzer.json")
            if not os.path.exists(report_path):
                # 尝试小写文件名（兼容大小写差异）
                report_path = str(cg_path.parent / f"{case_name}-report-pyanalyzer.json")

            # ── 定位 GT 文件 ──
            # GT 路径约定：benchmark/{bench_name}/{category}/{case}/callgraph.json
            gt_path = os.path.join(gt_bench, category, case_name, 'callgraph.json')
            if not os.path.exists(gt_path):
                skipped.append(f"{bench_name}/{category}/{case_name} (no GT)")
                continue

            # ── 加载预测调用图 ──
            try:
                pred = load_pyanalyzer_callgraph(cg_file, report_path, case_name)
            except Exception as e:
                skipped.append(f"{bench_name}/{category}/{case_name} (pred load error: {e})")
                continue

            # ── 加载 GT 调用图 ──
            try:
                gt = load_gt_callgraph(gt_path)
            except Exception as e:
                skipped.append(f"{bench_name}/{category}/{case_name} (gt load error: {e})")
                continue

            # ── 计算单个用例的 P/R/F1 ──
            if not gt:
                # GT 为空的边界情况：
                #   - pred 也为空 → 完美预测 (1,1,1)
                #   - pred 非空   → 全部误报 P=0
                if not pred:
                    p, r, f = 1.0, 1.0, 1.0
                else:
                    p, r, f = 0.0, 1.0, 0.0
            else:
                p, r, f = prf1(pred, gt)

            # ── 合并到 category 统计池 ──
            if category not in category_stats:
                category_stats[category] = {
                    'pred_pool': set(),  # 该类别所有用例的预测条目合并池
                    'gt_pool':   set(),  # 该类别所有用例的 GT 条目合并池
                    'cases':     []      # 该类别所有用例的详情列表
                }
            category_stats[category]['pred_pool'].update(pred)
            category_stats[category]['gt_pool'].update(gt)
            category_stats[category]['cases'].append({
                'case': case_name, 'pred': pred, 'gt': gt,
                'p': p, 'r': r, 'f': f,
            })

            # 收集 CSV 行数据
            csv_rows.append({
                'bench':    bench_name,
                'category': category,
                'case':     case_name,
                'pred':     len(pred),
                'gt':       len(gt),
                'tp':       len(pred & gt),       # 真正例
                'fp':       len(pred - gt),       # 假正例（误报）
                'fn':       len(gt - pred),       # 假负例（漏报）
                'P':        f'{p * 100:.1f}',
                'R':        f'{r * 100:.1f}',
                'F1':       f'{f * 100:.1f}',
            })

            if verbose:
                tp = pred & gt   # 命中的调用关系
                fp = pred - gt   # 误报的调用关系
                fn = gt - pred   # 漏报的调用关系
                print(f"\n[{category}/{case_name}]  P={p:.1%}  R={r:.1%}  F1={f:.1%}"
                      f"  (pred={len(pred)} gt={len(gt)} tp={len(tp)})")
                if fp: print(f"  FP({len(fp)}): {sorted(fp)[:3]}{'…' if len(fp) > 3 else ''}")
                if fn: print(f"  FN({len(fn)}): {sorted(fn)[:3]}{'…' if len(fn) > 3 else ''}")

    if not category_stats:
        print("[ERROR] No test cases found. Check directory paths.")
        return

    # ── 按论文 Table 7 的类别顺序排列输出 ──
    CATEGORY_ORDER = [
        'args', 'assignments', 'builtins', 'classes', 'dicts',
        'direct_calls', 'dynamic', 'functions', 'generators',
        'high_order_class', 'imports', 'kwargs', 'lambdas',
        'lists', 'mro', 'returns', 'unsure_lookup',
    ]
    # 按论文顺序排列已找到的类别，额外类别按字母序追加
    ordered_cats = [c for c in CATEGORY_ORDER if c in category_stats]
    extra_cats   = [c for c in category_stats if c not in CATEGORY_ORDER]
    ordered_cats += sorted(extra_cats)

    # ── 打印结果表 ──
    print("\n" + "=" * 62)
    print("  RQ2 PyAnalyzer — Table 7 (P/R/F1 by Category)")
    print("=" * 62)
    print(f"  {'Category':<20}  {'P':>7}  {'R':>7}  {'F1':>7}  {'Cases':>6}")
    print(f"  {'-' * 54}")

    # 全局合并池（用于计算 TOTAL 行）
    total_pred: CallSet = set()
    total_gt:   CallSet = set()

    for cat in ordered_cats:
        stat = category_stats[cat]
        pp = stat['pred_pool']  # 该类别预测合并池
        gg = stat['gt_pool']    # 该类别 GT 合并池
        cp, cr, cf = prf1(pp, gg)  # 该类别的全局 P/R/F1

        # 合并到全局池
        total_pred.update(pp)
        total_gt.update(gg)

        n = len(stat['cases'])  # 该类别的用例数
        print(f"  {cat:<20}  {cp * 100:>6.1f}%  {cr * 100:>6.1f}%  {cf * 100:>6.1f}%  {n:>6}")

    # 计算并输出 TOTAL 行
    tp_p, tp_r, tp_f = prf1(total_pred, total_gt)
    print(f"  {'-' * 54}")
    print(f"  {'TOTAL':<20}  {tp_p * 100:>6.1f}%  {tp_r * 100:>6.1f}%  {tp_f * 100:>6.1f}%  "
          f"{sum(len(s['cases']) for s in category_stats.values()):>6}")
    print("=" * 62)
    # 与论文参考值对比
    print("  论文 Table 7 参考值 (PyAnalyzer):")
    print("    TOTAL:  P=92.9%  R=96.1%  F1=94.5%")
    print("=" * 62)

    # 打印跳过的用例（最多显示5个）
    if skipped:
        print(f"\n  跳过 ({len(skipped)} 个): {skipped[:5]}{'...' if len(skipped) > 5 else ''}")

    # ── 输出 CSV 文件 ──
    if csv_path and csv_rows:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\n  CSV 已保存至: {csv_path}")


# =============================================================================
# 命令行入口
# =============================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='RQ2 PyAnalyzer Evaluator')

    # PyAnalyzer 结果根目录（默认 Data/RQ2/results/PyAnalyzer）
    parser.add_argument('--results',
        default=os.path.join(BASE, 'Data', 'RQ2', 'results', 'PyAnalyzer'),
        help='PyAnalyzer 结果根目录')

    # micro-benchmark B 根目录（含 callgraph.json GT 文件）
    parser.add_argument('--benchmark',
        default=os.path.join(BASE, 'Data', 'RQ2', 'micro-benchmark B'),
        help='micro-benchmark B 根目录（含 callgraph.json）')

    # 可选：将每个用例的详细结果输出到 CSV 文件
    parser.add_argument('--csv', default=None,
        help='输出 CSV 文件路径（可选）')

    # 详细模式：显示每个用例的 FP/FN 条目
    parser.add_argument('-v', '--verbose', action='store_true',
        help='显示每个 case 的详细 FP/FN')

    args = parser.parse_args()

    evaluate(
        results_root   = args.results,
        benchmark_root = args.benchmark,
        verbose        = args.verbose,
        csv_path       = args.csv,
    )