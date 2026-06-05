'''
This script aims to run automated usability test on selected dependency extraction tools.
（本脚本用于对多个依赖提取工具进行自动化效率测试，记录每个工具在各项目上的运行时间和峰值内存占用。）

This script depends on:
    Git (through environment path)
    SciTools Understand (through environment path)
    SourceTrail (through environment path)
    Java >= 15 (through environment path)
    Python >= 3.8 (through environment path)

    pip install psutil      // 用于内存占用监控
    pip install pyuserinput // 用于自动化 SourceTrail GUI

This script has only been tested on Windows 10/11.
'''

import re             # 正则表达式（本脚本未直接使用，保留为备用）
import io             # 流式 I/O，用于逐行读取子进程输出
import logging        # 日志模块，同时写文件和控制台
import signal         # 信号处理（本脚本未直接使用，保留为备用）
from os import path, rename  # path：路径操作；rename：文件重命名（完成标识）
import sys            # sys.exit()：遇到致命错误时退出
import csv            # 读取项目列表 CSV 文件
import subprocess     # 启动外部子进程（各分析工具）
import time           # 计时：time.time() 获取当前时间戳
import argparse       # 命令行参数解析
from datetime import datetime  # 生成带时间戳的日志/输出文件名
from threading import Timer, Thread  # Timer：超时控制；Thread：后台内存监控
from functools import reduce  # 汇总子进程内存（reduce 求和）
import os             # 文件系统操作


# =============================================================================
# get_files：递归收集指定目录下所有 .py 文件的路径，追加到全局列表 py_list。
# =============================================================================
def get_files(target_dir):
    global py_list
    for filename in os.listdir(target_dir):
        filepath = os.path.join(target_dir, filename)
        if os.path.isdir(filepath):
            get_files(filepath)   # 递归进入子目录
        elif os.path.isfile(filepath) and filename.endswith(".py"):
            py_list.append(filepath)  # 收集 .py 文件路径


# 生成时间戳字符串（格式：YYMMDDHHMM），用于日志和输出文件命名，避免覆盖历史记录
timestamp = datetime.now().strftime("%y%m%d%H%M")

# -------------------------------------------------------------------------
# 日志配置：同时输出到文件（./logs/<timestamp>.log）和控制台
# -------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(f'./logs/{timestamp}.log'),  # 文件日志
        logging.StreamHandler()                           # 控制台日志
    ]
)

# 尝试导入 psutil（内存监控库），若未安装则降级禁用内存监控
try:
    import psutil
except NameError:
    logging.warning('Can not import psutil, memory usage monitoring disabled')


# =============================================================================
# memory_profiling：在后台线程中持续监控指定 PID 的内存占用（含所有子进程），
#                   记录峰值内存（MB），并在超过 20GB 阈值时强制杀死进程。
# 参数：pid —— 被监控进程的 PID
# 返回：value 字典（{'peak': 峰值内存(MB) 或 -1（未安装psutil）}）
#       调用方可在进程结束后读取 value['peak']
# =============================================================================
def memory_profiling(pid):
    value = {'peak': -1}  # 初始化峰值为 -1（表示未知或未启用）

    def task(pid, value):
        if psutil is None:
            value['peak'] = -1  # psutil 不可用，直接返回
            return value

        while True:
            prev = value['peak']  # 上一轮峰值
            try:
                psection = True  # 标记当前是否处于可能抛出进程不存在异常的代码段
                me = psutil.Process(pid)                    # 获取目标进程对象
                curr = me.memory_info().rss                 # 主进程当前内存（Bytes）
                psection = False

                # 累加所有子进程（递归）的内存占用
                children = me.children(recursive=True)
                curr += reduce(lambda p, v: p + v,
                               map(lambda sp: sp.memory_info().rss, children), 0)

                curr /= 1024 ** 2  # 单位转换：Bytes → MB

            except (psutil.ProcessLookupError, psutil.NoSuchProcess):
                if psection is True:
                    # 进程已结束（正常或被杀死），退出监控循环
                    logging.warning(f'Losing process with pid={pid}')
                    break
                else:
                    # 子进程消失（正常情况），抑制异常继续监控主进程
                    pass
            else:
                # 更新峰值（只增不减）
                value['peak'] = curr if curr > prev else prev

                # 内存超过 20GB 时强制杀死进程（防止 Windows 11 上的内存泄漏问题）
                if value['peak'] > 1024 * 20:
                    for c in children:
                        c.kill()  # 先杀子进程
                    me.kill()     # 再杀主进程
                    value['peak'] = 0
                    logging.warning(
                        f'The process with pid={pid} took too much memory and thus been killed')
                    break

                # 每 0.5 秒采样一次（兼顾精度与性能）
                time.sleep(0.5)

    # 在后台守护线程中启动监控任务
    Thread(target=task, args=(pid, value,)).start()
    return value  # 立即返回 value 字典引用，调用方可稍后读取峰值


# -------------------------------------------------------------------------
# 命令行参数定义
# -------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('lang', help='Sepcify the target language')          # 目标语言（如 python）
parser.add_argument('range', help='Specify the start line from csv file') # 项目列表行范围（如 "0-9"）
parser.add_argument('only', help='Specify the only tool to run', nargs='?')  # 可选：指定单个工具运行
parser.add_argument('-t', '--timeout',
                    help='Specify the maximum duration of a single process',
                    type=int)                                              # 可选：超时时间（秒）
args = parser.parse_args()

lang = args.lang  # 语言参数

# -------------------------------------------------------------------------
# 解析 range 参数：支持单行（"3"）和范围（"3-10"）两种格式
# -------------------------------------------------------------------------
range = args.range.split('-')
if len(range) == 1:
    from_line = int(range[0])  # 单行：起止行相同
    end_line = int(range[0])
elif len(range) == 2:
    from_line = int(range[0])  # 范围：起始行
    end_line = int(range[1])   # 终止行
else:
    raise ValueError(f'Invalid range format {args.range}, only support x or x-x')

# -------------------------------------------------------------------------
# 解析 only 参数：指定只运行某一工具（空字符串表示运行全部）
# -------------------------------------------------------------------------
only = args.only.lower() if args.only is not None else ''
try:
    # 校验 only 值是否在允许的工具名列表中
    ['clone', 'loc', 'depends', 'pysonar2', 'pycg', 'enre19',
     'understand', 'sourcetrail', 'pyanalyzer', ''].index(only)
except ValueError:
    raise ValueError(
        f'Invalid tool {only}, only support depends / pyanalyzer / understand / sourcetrail / clone / loc / PySonar2 / enre19 / PyCG')

# -------------------------------------------------------------------------
# 超时时间校验（单位：秒）
# -------------------------------------------------------------------------
timeout = args.timeout  # None 表示不限时
if timeout is not None:
    if timeout < 0 or timeout > 3600:
        raise ValueError(f'Invalid timeout value {timeout}, only range(300, 3600) are valid')
    if timeout < 300:
        logging.warning(
            f'Unrecommended timeout value {timeout}, this value is too low to get useful information, '
            f'and is allowed only for debug purpose')

# 输出本次运行的配置摘要到日志
logging.info(
    f'Working on {from_line}-{end_line} for {lang}'
    + f' with {"all tools" if only == "" else f"{only} only"}'
    + (f' and timeout limit to {timeout}' if timeout is not None else ''))

# 输出文件路径：records/<timestamp>-<lang>-<from>-<end>.csv
outfile_path = f'./records/{timestamp}-{lang}-{from_line}-{end_line}.csv'

# -------------------------------------------------------------------------
# 初始化输出文件（使用 .pending 后缀，脚本完成后去掉后缀以标识成功）
# -------------------------------------------------------------------------
with open(f'{outfile_path}.pending', 'a+') as f:
    f.write(
        'project_name'
        + ',LoC'
        + ',Depends-time'
        + ',Depends-memory'
        + ',SourceTrail-time'
        + ',SourceTrail-memory'
        + ',Understand-time'
        + ',Understand-memory'
        + ',PySonar2-time'
        + ',PySonar2-memory'
        + ',PyCG-time'
        + ',PyCG-memory'
        + ',ENRE19-time'
        + ',ENRE19-memory'
        + ',pyanalyzer-time'
        + ',pyanalyzer-memory'
        + '\n')

# -------------------------------------------------------------------------
# 读取项目列表 CSV，按 range 参数筛选出需要处理的项目名称
# -------------------------------------------------------------------------
project_clone_url_list = dict()  # {项目名: 行号}
try:
    with open(f'./lists/python_project_list.csv', 'r', encoding='latin-1') as file:
        project_list = csv.reader(file)
        count = 0
        for row in project_list:
            if (count >= from_line) and (count <= end_line):
                project_name = row[0]                         # 第一列为项目名
                project_clone_url_list[project_name] = count  # 记录行号
            count += 1
except EnvironmentError:
    logging.error(f'Can not find project list for {args.lang}')
    sys.exit()  # 无法读取项目列表时终止脚本

# =============================================================================
# 主循环：对每个项目依次运行各工具，记录耗时和内存
# =============================================================================
for project_name in project_clone_url_list.keys():
    print(project_name)
    repo_path = f'./repo/{project_name}'  # 本地仓库路径
    from pathlib import Path
    # 构造绝对路径（用于传给各工具命令行参数）
    abs_repo_path = Path(str(path.dirname(__file__)) + str(f'\\repo\{project_name}'))

    logging.info(f'Reusing existed local repository for {project_name}')
    records = dict()  # 存储本项目各工具的耗时和内存记录

    # -------------------------------------------------------------------------
    # 统计代码行数（LoC）：使用 cloc 工具，仅统计 Python 行数
    # -------------------------------------------------------------------------
    if only == 'loc' or only == '':
        print('Counting line of code')
        LoC = 0
        cmd = f'.\\utils\\cloc-1.92.exe {repo_path} --csv --quiet'  # cloc 输出 CSV 格式
        try:
            # 仅在"运行全部工具"模式下设置 180s 超时，单独统计 LoC 时不限时
            proc = subprocess.check_output(cmd, timeout=180 if only == '' else None)
        except subprocess.TimeoutExpired:
            logging.exception(f'Counting LoC for {project_name} timed out')
            records['LoC'] = -1
        except subprocess.CalledProcessError:
            logging.exception(f'Failed couting line of code for {project_name}')
            records['LoC'] = -1
        else:
            outs = proc.strip().decode('utf-8').splitlines()
            for out in outs:
                out = out.split(',')
                if len(out) == 5:
                    if out[1] == 'Python':       # 只统计 Python 语言的代码行
                        LoC += int(out[-1])      # cloc CSV 最后一列为代码行数
            logging.info(f'LoC for {project_name} is {LoC}')
        records['LoC'] = LoC
    else:
        records['LoC'] = 0  # 不统计 LoC 时填 0

    # -------------------------------------------------------------------------
    # 运行 PyAnalyzer
    # -------------------------------------------------------------------------
    if only == 'pyanalyzer' or only == '':
        print('Starting pyanalyzer')
        # 构造命令：对仓库目录生成 CFG 和调用图
        cmd = f'{path.join(path.dirname(__file__), "./tools/pyanalyzer/pyanalyzer.exe")} {abs_repo_path} --cfg --cg'
        print(cmd)

        time_start = time.time()  # 计时开始
        proc = subprocess.Popen(
            cmd,
            # 注意：不使用 shell=True，避免产生 shell → 工具的多层子进程，
            # 防止 kill() 只杀死 shell 而非工具本身
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,           # 将 stderr 合并到 stdout
            cwd=f'./out/pyanalyzer-{lang}')     # 工作目录（输出文件将写到此处）

        killed = False  # 标记是否被超时杀死

        if timeout is not None:
            def handle_timeout():
                global killed
                killed = True
                proc.kill()
                logging.warning(f'Running pyanalyzer-{lang} on {project_name} timed out')
                records['pyanalyzer-time'] = -1  # -1 表示超时

            timer = Timer(timeout, handle_timeout)  # 设定超时计时器
            timer.start()

        pid = proc.pid
        memory = memory_profiling(pid)  # 启动后台内存监控

        # 逐行读取工具的 stdout 并打印（同时等待进程结束）
        try:
            for line in io.TextIOWrapper(proc.stdout):
                print(line, end='')
        except UnicodeDecodeError:
            logging.warning(f'Suppressing an encoding error on pyanalyzer-{lang} while process {project_name}')

        time_end = time.time()  # 计时结束

        records['pyanalyzer-memory'] = memory['peak']  # 无论是否超时都记录峰值内存

        if not killed:
            try:
                logging.info(f'pyanalyzer-{lang} finished normally, cancel the timer')
                timer.cancel()  # 进程正常结束，取消超时计时器
            except:
                pass
            records['pyanalyzer-time'] = time_end - time_start
            logging.info(
                f'Running pyanalyzer-{lang} on {project_name} costs {records["pyanalyzer-time"]}s'
                + (f' and {records["pyanalyzer-memory"]}MB' if records['pyanalyzer-memory'] != -1 else ''))
    else:
        records['pyanalyzer-time'] = 0
        records['pyanalyzer-memory'] = 0

    # -------------------------------------------------------------------------
    # 运行 ENRE19（Java 实现的实体关系提取工具）
    # -------------------------------------------------------------------------
    if only == 'enre19' or only == '':
        print('Starting ENRE19')
        if args.lang == 'python':
            cmd = f'java -jar ./tools/ENRE19/ENRE-v2.0.jar python {abs_repo_path} null {project_name}'
        print(cmd)

        time_start = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)

        killed = False
        if timeout is not None:
            def handle_timeout():
                global killed
                killed = True
                proc.kill()
                logging.warning(f'Running ENRE19 on {project_name} timed out')
                records['ENRE19-time'] = -1

            timer = Timer(timeout, handle_timeout)
            timer.start()

        pid = proc.pid
        memory = memory_profiling(pid)
        try:
            for line in io.TextIOWrapper(proc.stdout):
                print(line, end='')
        except UnicodeDecodeError:
            logging.warning(f'Suppressing an encoding error on ENRE19 while process {project_name}')
        time_end = time.time()

        records['ENRE19-memory'] = memory['peak']
        if not killed:
            try:
                logging.info(f'ENRE19 finished normally, cancel the timer')
                timer.cancel()
            except:
                pass
            records['ENRE19-time'] = time_end - time_start
            logging.info(
                f'Running ENRE19 on {project_name} costs {records["ENRE19-time"]}s'
                + (f' and {records["ENRE19-memory"]}MB' if records['ENRE19-memory'] != -1 else ''))
    else:
        records['ENRE19-time'] = 0
        records['ENRE19-memory'] = 0

    # -------------------------------------------------------------------------
    # 运行 PySonar2（Java 实现的 Python 静态分析工具）
    # -------------------------------------------------------------------------
    if only == 'pysonar2' or only == '':
        print('Starting PySonar2')
        if args.lang == 'python':
            project_set_path = Path(str(path.dirname(__file__)) + '\\tools\\pysonar-2.1.3.jar')
            # 使用 classpath 启动 PySonar2 的 JSONDump 入口类
            cmd = f'java -classpath {project_set_path} org.yinwang.pysonar.JSONDump {abs_repo_path} : {project_name}'
        print(cmd)

        time_start = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)

        killed = False
        if timeout is not None:
            def handle_timeout():
                global killed
                killed = True
                proc.kill()
                logging.warning(f'Running PySonar2 on {project_name} timed out')
                records['PySonar2-time'] = -1

            timer = Timer(timeout, handle_timeout)
            timer.start()

        pid = proc.pid
        memory = memory_profiling(pid)
        try:
            for line in io.TextIOWrapper(proc.stdout):
                print(line, end='')
        except UnicodeDecodeError:
            logging.warning(f'Suppressing an encoding error on PySonar2 while process {project_name}')
        time_end = time.time()

        records['PySonar2-memory'] = memory['peak']
        if not killed:
            try:
                logging.info(f'PySonar2 finished normally, cancel the timer')
                timer.cancel()
            except:
                pass
            records['PySonar2-time'] = time_end - time_start
            logging.info(
                f'Running PySonar2 on {project_name} costs {records["PySonar2-time"]}s'
                + (f' and {records["PySonar2-memory"]}MB' if records['PySonar2-memory'] != -1 else ''))
    else:
        records['PySonar2-time'] = 0
        records['PySonar2-memory'] = 0

    # -------------------------------------------------------------------------
    # 运行 PyCG（Python 调用图分析工具，通过 Git Bash 调用以支持 find 命令）
    # -------------------------------------------------------------------------
    if only == 'pycg':
        print('Starting pycg')
        path1 = str(abs_repo_path).replace("\\", "/")  # 将 Windows 路径转为 Unix 格式
        if args.lang == 'python':
            # 进入项目目录，用 find 收集所有 .py 文件，通过 --package 指定包名分析
            cmd = f'cd {path1} && pycg --package {project_name} $(find . -type f -name "*.py") -o {project_name}.json'
        print("cmd = " + cmd)

        time_start = time.time()
        # 使用 Git 自带的 bash 执行命令（Windows 环境下支持 find/pycg）
        proc = subprocess.Popen(['D:\\git\\Git\\bin\\bash', '-c', cmd],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        killed = False

        if timeout is not None:
            def handle_timeout():
                global killed
                killed = True
                proc.kill()
                logging.warning(f'Running PyCG on {project_name} timed out')
                records['PyCG-time'] = -1

            timer = Timer(timeout, handle_timeout)
            timer.start()

        pid = proc.pid
        memory = memory_profiling(pid)

        try:
            for line in io.TextIOWrapper(proc.stdout):
                print(line, end='')
        except UnicodeDecodeError:
            logging.warning(f'Suppressing an encoding error on PyCG while process {project_name}')

        time_end = time.time()
        print(killed)  # 调试输出：是否超时被杀死

        records['PyCG-memory'] = memory['peak']
        if not killed:
            try:
                logging.info(f'PyCG finished normally, cancel the timer')
                timer.cancel()
            except:
                pass
            records['PyCG-time'] = time_end - time_start
            logging.info(
                f'Running PyCG on {project_name} costs {records["PyCG-time"]}s'
                + (f' and {records["PyCG-memory"]}MB' if records['PyCG-memory'] != -1 else ''))
        else:
            continue  # 超时则跳过本项目，继续处理下一个
    else:
        records['PyCG-time'] = 0
        records['PyCG-memory'] = 0

    # -------------------------------------------------------------------------
    # 运行 Depends（Java 实现的多语言依赖分析工具）
    # -------------------------------------------------------------------------
    if only == 'depends' or only == '':
        print('starting Depends')
        # -g var：生成变量级别的依赖关系
        cmd = f'java -jar {path.join(path.dirname(__file__), "./tools/depends.jar")} {lang} {abs_repo_path} {project_name} -g var'

        time_start = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd='./out/depends')  # 输出到 ./out/depends 目录

        killed = False
        if timeout is not None:
            def handle_timeout():
                global killed
                killed = True
                proc.kill()
                logging.warning(f'Running Depends on {project_name} timed out')
                records['Depends-time'] = -1

            timer = Timer(timeout, handle_timeout)
            timer.start()

        pid = proc.pid
        memory = memory_profiling(pid)
        try:
            for line in io.TextIOWrapper(proc.stdout):
                print(line, end='')
        except UnicodeDecodeError:
            logging.warning(f'Suppressing an encoding error on Depends while process {project_name}')
        time_end = time.time()

        records['Depends-memory'] = memory['peak']
        if not killed:
            try:
                logging.info('Depends finished normally, cancel the timer')
                timer.cancel()
            except:
                pass
            records['Depends-time'] = time_end - time_start
            logging.info(
                f'Running Depends on {project_name} costs {records["Depends-time"]}s'
                + (f' and {records["Depends-memory"]}MB' if records['Depends-memory'] != -1 else ""))
    else:
        records['Depends-time'] = 0
        records['Depends-memory'] = 0

    # -------------------------------------------------------------------------
    # 运行 Understand（SciTools 商业代码分析工具，通过命令行 und 调用）
    # -------------------------------------------------------------------------
    if only == 'understand' or only == '':
        print('Starting Understand')
        upath = f'./out/understand/{project_name}.und'  # Understand 数据库路径
        ulang = 'Python'
        # und 命令：创建数据库 → 添加源码目录 → 分析全部
        cmd = f'und create -db {upath} -languages {ulang} add {abs_repo_path} analyze -all'

        time_start = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd='./out/understand')  # 工作目录（upath 已包含完整路径，cwd 仅为保险）

        killed = False
        if timeout is not None:
            def handle_timeout():
                global killed
                killed = True
                proc.kill()
                logging.warning(f'Running Understand on {project_name} timed out')
                records['Understand-time'] = -1

            timer = Timer(timeout, handle_timeout)
            timer.start()

        pid = proc.pid
        memory = memory_profiling(pid)
        try:
            for line in io.TextIOWrapper(proc.stdout):
                print(line, end='')
        except UnicodeDecodeError:
            logging.warning(f'Suppressing an encoding error on Understand while process {project_name}')
        time_end = time.time()

        records['Understand-memory'] = memory['peak']
        if not killed:
            try:
                logging.info('Process finished normally, cancel the timer')
                timer.cancel()
            except:
                pass
            records['Understand-time'] = time_end - time_start
            logging.info(
                f'Running Understand on {project_name} costs {records["Understand-time"]}s'
                + (f' and {records["Understand-memory"]}MB' if records['Understand-memory'] != -1 else ""))
    else:
        records['Understand-time'] = 0
        records['Understand-memory'] = 0

    # -------------------------------------------------------------------------
    # 运行 SourceTrail（仅在 only == 'sourcetrail' 时运行；要求项目已预先创建）
    # -------------------------------------------------------------------------
    if only == 'sourcetrail':
        print('Start SourceTrail')
        spath = f'./out/sourcetrail/{project_name}.srctrlprj'  # SourceTrail 项目文件

        if path.exists(spath):
            # 使用命令行索引模式运行 SourceTrail
            cmd = f'D:\\test\\sourcetrail\\Sourcetrail.exe index -f {path.join(path.dirname(__file__), spath)}'
            print(cmd)
            time_start = time.time()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT)

            killed = False
            if timeout is not None:
                def handle_timeout():
                    global killed
                    killed = True
                    proc.kill()
                    logging.warning(f'Running Sourcetrail on {project_name} timed out')
                    records['SourceTrail-time'] = -1

                timer = Timer(timeout, handle_timeout)
                timer.start()

            pid = proc.pid
            memory = memory_profiling(pid)
            try:
                for line in io.TextIOWrapper(proc.stdout):
                    print(line, end='')
            except UnicodeDecodeError:
                logging.warning(f'Suppressing an encoding error on SourceTrail while process {project_name}')
            time_end = time.time()

            records['SourceTrail-memory'] = memory['peak']
            if not killed:
                try:
                    logging.info('Process finished normally, cancel the timer')
                    timer.cancel()
                except:
                    pass
                records['SourceTrail-time'] = time_end - time_start
                logging.info(
                    f'Running SourceTrail on {project_name} costs {records["SourceTrail-time"]}s'
                    + (f' and {records["SourceTrail-memory"]}MB' if records['SourceTrail-memory'] != -1 else ""))
        else:
            # 若 SourceTrail 项目文件不存在（需手动创建），则跳过该项目
            logging.warning(f'No SourceTrail project for {project_name}, skipped')
            records['SourceTrail-time'] = 0
            records['SourceTrail-memory'] = 0
    else:
        records['SourceTrail-time'] = 0
        records['SourceTrail-memory'] = 0

    # -------------------------------------------------------------------------
    # 每处理完一个项目，立即将结果追加写入 .pending 文件
    # （防止脚本中途崩溃导致已完成的数据丢失）
    # -------------------------------------------------------------------------
    with open(f'{outfile_path}.pending', 'a+') as f:
        f.write(
            f'{project_name}'
            + f',{records["LoC"]}'
            + f',{records["Depends-time"]}'
            + f',{records["Depends-memory"]}'
            + f',{records["SourceTrail-time"]}'
            + f',{records["SourceTrail-memory"]}'
            + f',{records["Understand-time"]}'
            + f',{records["Understand-memory"]}'
            + f',{records["PySonar2-time"]}'
            + f',{records["PySonar2-memory"]}'
            + f',{records["PyCG-time"]}'
            + f',{records["PyCG-memory"]}'
            + f',{records["ENRE19-time"]}'
            + f',{records["ENRE19-memory"]}'
            + f',{records["pyanalyzer-time"]}'
            + f',{records["pyanalyzer-memory"]}'
            + '\n')

# -------------------------------------------------------------------------
# 所有项目处理完毕后，将 .pending 文件重命名为最终文件（去掉 .pending 后缀）
# 若重命名失败，说明脚本运行期间发生了异常，需检查日志
# -------------------------------------------------------------------------
try:
    rename(f'{outfile_path}.pending', outfile_path)
except:
    logging.error(f'Lost output file {outfile_path}!')

logging.info('Run has completed')