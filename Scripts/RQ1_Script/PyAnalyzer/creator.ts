// =============================================================================
// creator.ts
// 功能：在测试目录下为指定测试用例（group/case）创建输出目录，
//       然后调用 PyAnalyzer 可执行文件对该用例的源码进行分析。
//       若分析成功返回 true，否则返回 false。
// =============================================================================

import exec from '../../../common/exec'; // 封装的异步命令执行工具
import {mkdir} from 'fs/promises';       // 异步创建目录

// 导出默认异步函数
// 参数：g       —— 测试组名（group）
//       c       —— 测试用例名（case）
//       exepath —— PyAnalyzer 可执行文件路径
export default async (g: string, c: string, exepath: string) => {
  const ocwd = process.cwd();  // 保存当前工作目录（以便分析完成后恢复）

  // 创建 PyAnalyzer 输出目录（递归创建，若已存在不报错）
  await mkdir(ocwd + `/tests/pyanalyzer/${g}/${c}`, {recursive: true});

  // 切换到输出目录（PyAnalyzer 会将结果写到当前工作目录）
  process.chdir(ocwd + `/tests/pyanalyzer/${g}/${c}`);

  try {
    // 调用 PyAnalyzer 分析对应的测试用例源码目录
    // 用例源码路径约定为：<工作目录>/tests/cases/_<g>/_<c>
    await exec(`${exepath} ${ocwd}/tests/cases/_${g}/_${c}`);
  } catch {
    return false;  // 执行失败（进程非零退出或异常）：返回 false
  } finally {
    process.chdir(ocwd);  // 无论成功或失败，均恢复原始工作目录
  }

  return true;  // 分析成功
};