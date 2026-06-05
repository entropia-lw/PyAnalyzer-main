# =============================================================================
# batch_process.py
# 功能：读取 Data/command.json 配置文件，按配置批量构造并执行多条系统命令，
#       实现对多个语言/项目的批量分析处理。
# =============================================================================

import json  # 解析 JSON 配置文件
import os    # 执行系统命令


def execute(content: dict):
    """
    根据单条配置项构造命令列表并循环执行。
    content 字段说明：
      - script:          要运行的脚本路径（命令第一部分）
      - language:        语言名称列表（每次迭代替换）
      - project:         项目名称列表（每次迭代替换）
      - input_path:      输入路径
      - output_path:     输出路径
      - field_separator: 字段分隔符
    """
    # 初始化命令列表：使用第一个 language 和 project 作为占位初始值
    commandList = [content['script'], content['language'][0], content['project'][0],
                   content['input_path'], content['output_path'], content['field_separator']]

    # 遍历所有项目（language 和 project 列表等长）
    for iter in range(len(content['project'])):
        # 替换命令列表中第 1、2 位（language 和 project）为当前迭代值
        commandList[1:3] = content['language'][iter], content['project'][iter]

        command = " ".join(commandList)  # 将命令列表拼接为字符串

        print(f"Executing: {command}")
        os.system(command)  # 执行命令（阻塞，等待完成）


if __name__ == "__main__":

    command = 'Data/command.json'  # 配置文件路径
    with open(command, 'r', encoding='utf-8') as f:
        data = json.load(f)  # 加载 JSON 配置

    # 遍历配置文件中的每一条配置项（key 为标识，value 为配置内容）
    for key in data.keys():
        value = data[key]
        execute(value)  # 执行本条配置的批量命令

    print("Batch processing completed.")