import re
from datetime import datetime, timezone, timedelta

def main():
    # 1. 获取当前的北京时间 (UTC+8)
    tz_beijing = timezone(timedelta(hours=8))
    update_time = datetime.now(tz_beijing).strftime('%Y-%m-%d %H:%M:%S')

    # 2. 读取测速结果
    try:
        with open('result.txt', 'r', encoding='utf-8') as f:
            result_content = f.read()
    except FileNotFoundError:
        print("未找到 result.txt，可能测速脚本未生成数据。")
        return

    # 3. 读取原始 README
    with open('README.md', 'r', encoding='utf-8') as f:
        readme = f.read()

    # 4. 组装带时间戳和代码块的 Markdown 文本
    new_text = f"\n> 🕒 **最后更新时间:** {update_time} (北京时间)\n\n```text\n{result_content}\n```\n"

    # 5. 使用正则替换标签中间的内容
    new_readme = re.sub(r'.*?', new_text, readme, flags=re.DOTALL)

    # 6. 覆写 README
    with open('README.md', 'w', encoding='utf-8') as f:
        f.write(new_readme)
    
    print("✅ README.md 更新成功！")

if __name__ == '__main__':
    main()
