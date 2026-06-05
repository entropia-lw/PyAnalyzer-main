// =============================================================================
// index.ts
// 功能：PyAnalyzer 测试适配器的主入口。
//   执行流程（带缓存优化）：
//     1. 尝试直接读取已有的 PyAnalyzer 报告 JSON（缓存命中路径）
//     2. 若文件不存在，则调用 creator 运行 PyAnalyzer 生成报告
//     3. 再调用 extractor 读取刚生成的报告
//     4. 将报告内容传给 builder 解析，注册实体和关系到全局容器
//     5. 调用 UNIMatcher 对测试用例进行断言匹配，返回结果
// =============================================================================

import creator from './creator';           // 运行 PyAnalyzer 生成报告
import extractor from './extractor';       // 读取 PyAnalyzer 报告文件
import builder from './builder';           // 解析报告，注册实体和关系
import {error} from '@pyanalyzer/logging'; // 日志：错误输出
import {CaseContainer} from '@pyanalyzer/doc-parser'; // 测试用例容器类型
import {UNIMatcher} from '../../../matchers';          // 统一断言匹配器
import {readFile} from 'node:fs/promises'; // 异步文件读取

// 导出默认异步函数
// 参数：g       —— 测试组名
//       c       —— 测试用例名
//       cs      —— 测试用例容器（含预期断言）
//       ocwd    —— 原始工作目录
//       exepath —— PyAnalyzer 可执行文件路径
export default async (g: string, c: string, cs: CaseContainer, ocwd: string, exepath: string) => {
  try {
    // -----------------------------------------------------------------------
    // 快速路径（缓存命中）：直接读取已有的报告文件，避免重复运行 PyAnalyzer
    // 报告文件路径约定：<cwd>/tests/pyanalyzer/<g>/<c>/_<c>-report-pyanalyzer.json
    // -----------------------------------------------------------------------
    const data = await readFile(
      `${process.cwd()}/tests/pyanalyzer/${g}/${c}/_${c}-report-pyanalyzer.json`,
      'utf-8'
    );

    // console.log(data.replaceAll(/\s+/g, ' ')); // 调试用：压缩空白后打印（已注释）

    builder(data);                         // 解析报告，注册实体和关系到全局容器
    return UNIMatcher(cs, 'python', 'e');  // 执行断言匹配，返回匹配结果

  } catch {
    // -----------------------------------------------------------------------
    // 慢速路径（缓存未命中）：报告文件不存在，需先运行 PyAnalyzer 生成
    // -----------------------------------------------------------------------
    if (await creator(g, c, exepath)) {
      // PyAnalyzer 运行成功：读取刚生成的报告文件
      const data = await extractor(g, c, ocwd);
      if (data) {
        // console.log(data.replaceAll(/\s+/g, ' ')); // 调试用（已注释）
        builder(data);                         // 解析并注册
        return UNIMatcher(cs, 'python', 'e');  // 匹配断言
      } else {
        // 报告文件读取失败（生成了但读取出错）
        error(`Failed to read pyanalyzer output on ${g}/${c}`);
      }
    } else {
      // PyAnalyzer 运行本身失败（非零退出或异常）
      error(`Failed to execute pyanalyzer on ${g}/${c}`);
    }
    // 两种失败情况均返回 undefined（调用方需处理 undefined 返回值）
  }
};