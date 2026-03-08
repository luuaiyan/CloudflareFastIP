import os
import re
import time
from collections import defaultdict
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

def get_ips_from_domain(page, domain):
    """从域名页面提取 IP"""
    url = f'https://www.itdog.cn/ping/{domain}'
    try:
        page.goto(url)
        page.wait_for_timeout(2000) 
        
        try:
            # 自动点击测试按钮
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
            print(f"  👆 成功触发域名 {domain} 的测试！")
        except Exception:
            pass
            
        page.wait_for_timeout(5000) 
        
        html = page.content()
        soup = BeautifulSoup(html, 'lxml')
        ip_list = []
        
        for a in soup.select('ul.ip_list a'):
            onclick = a.get('onclick', '')
            match = re.search(r"filter_ip\('([^']+)'\)", onclick)
            if match:
                ip = match.group(1)
                # 过滤掉无效字符
                if ip and ip != '解析失败':
                    ip_list.append(ip.strip())
        
        if not ip_list:
            page.screenshot(path=f"error_{domain}.png")
            
        return list(set(ip_list))
    except Exception as e:
        print(f'❌ 域名 {domain} 访问异常: {e}')
        return []

def get_ping_data_for_ip(page, ip):
    """测试单个 IP 的全国及海外节点延迟"""
    url = f'https://www.itdog.cn/ping/{ip}'
    try:
        page.goto(url)
        page.wait_for_timeout(2000) 
        
        try:
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
        except Exception:
            pass
            
        # 等待 10 秒获取初步结果
        page.wait_for_timeout(10000) 
        
        html = page.content()
        soup = BeautifulSoup(html, 'lxml')
        results = []
        
        for tr in soup.select('tr.node_tr'):
            node = tr.get('node')
            node_type = tr.get('node_type')
            if not node or not node_type:
                continue
                
            ping_td = tr.find('td', id=f'ping_{node}')
            latency = ping_td.text.strip() if ping_td else 'N/A'
            
            results.append({
                'node_type': node_type,
                'latency': latency
            })
        return results
    except Exception as e:
        print(f'❌ IP {ip} 测试异常: {e}')
        return []

def print_top5_table(provider_name, data_list):
    """在终端绘制漂亮的 ASCII 表格，只显示 Top 5"""
    print(f"\n🚀 【{provider_name}】最优 IP 排行 (Top 5):")
    print("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    print("| 排名 | IP 地址              | 平均延迟     |")
    print("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    
    if not data_list:
        print("|" + "无有效测速数据".center(42, " ") + "|")
    else:
        # 对延迟进行从小到大排序
        top5 = sorted(data_list, key=lambda x: x['avg_latency'])[:5]
        for idx, item in enumerate(top5, 1):
            ip_str = item['ip'].ljust(20)
            avg_str = f"{item['avg_latency']:.2f} ms".rjust(12)
            print(f"| {idx:<4} | {ip_str} | {avg_str} |")
            
    print("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")

def main():
    file_path = 'domains.txt'
    if not os.path.exists(file_path):
        print(f"❌ 找不到文件: {file_path}")
        return
        
    with open(file_path, 'r', encoding='utf-8') as f:
        domains = [line.strip() for line in f if line.strip()]

    if not domains:
        print(f"⚠️ '{file_path}' 是空的。")
        return

    print(f"📁 成功加载 {len(domains)} 个域名。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        stealth_sync(page)

        # --- 第一阶段：实时去重提取 ---
        print("\n🔎 第一阶段：开始提取 IP (实时去重)...")
        all_ips = set()
        for domain in domains:
            print(f'  正在解析: {domain}')
            ips = get_ips_from_domain(page, domain)
            
            # 找出当前域名解析出、但还没在 all_ips 里的新 IP
            new_found = [ip for ip in ips if ip not in all_ips]
            if new_found:
                print(f"    ✨ 发现 {len(new_found)} 个新唯一 IP")
                all_ips.update(new_found)
            else:
                print(f"    ℹ️ 未发现新 IP (已在库中)")
            time.sleep(1) 

        # 格式清洗：确保只保留合法的 IPv4 格式
        final_list = [ip for ip in all_ips if len(ip.split('.')) == 4]
        print(f'\n✅ 提取完成！共获取到 {len(final_list)} 个唯一 IP 待测\n')
        
        if not final_list:
            print("🛑 未提取到任何有效 IP，程序终止。")
            browser.close()
            return

        # --- 第二阶段：多节点测速 ---
        print("📡 第二阶段：开始延迟并发测试 (每个IP等待 10 秒)...")
        ip_data = {}
        for idx, ip in enumerate(final_list, 1):
            print(f'  [{idx}/{len(final_list)}] 正在测速 IP: {ip} ...')
            data = get_ping_data_for_ip(page, ip)
            ip_data[ip] = data
            time.sleep(1) 

        # --- 第三阶段：数据聚合 ---
        provider_map = {'1': '电信', '2': '联通', '3': '移动', '5': '海外'}
        aggregated_stats = {name: [] for name in provider_map.values()}
        
        for ip, nodes in ip_data.items():
            if not nodes: continue
            grouped_lats = defaultdict(list)
            for n in nodes:
                p_name = provider_map.get(n['node_type'])
                if p_name and 'ms' in n['latency']:
                    try:
                        grouped_lats[p_name].append(float(n['latency'].replace('ms', '').strip()))
                    except ValueError: pass
            
            for p_name, lats in grouped_lats.items():
                if lats:
                    avg = sum(lats) / len(lats)
                    aggregated_stats[p_name].append({'ip': ip, 'avg_latency': avg})

        # --- 第四阶段：成果展示 ---
        print('\n' + '=' * 50)
        print('🏆 优选 IP 最终测速报告 (四大线路 Top 5)')
        print('=' * 50)
        
        for p_name in ['电信', '联通', '移动', '海外']:
            print_top5_table(p_name, aggregated_stats[p_name])

        browser.close()
        print("\n🎉 任务圆满完成！")

if __name__ == '__main__':
    main()
