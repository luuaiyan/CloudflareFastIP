import os
import re
import sys
import time
import concurrent.futures
from collections import defaultdict
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
from tqdm import tqdm

# ================= 配置区 =================
MAX_WORKERS = 3  # 降到 3，防止被 ITDog 识别为 CC 攻击导致全部超时
WAIT_TIME = 10000  # 测速等待时间 10 秒
TIMEOUT_MS = 15000 # 页面加载超时 15 秒（关键优化：遇死节点快速跳过）
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# ==========================================

def get_ips_from_domain(page, domain):
    url = f'https://www.itdog.cn/ping/{domain}'
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        page.wait_for_timeout(2000) 
        try:
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
        except: pass
        page.wait_for_timeout(5000) 
        html = page.content()
        soup = BeautifulSoup(html, 'lxml')
        ip_list = []
        for a in soup.select('ul.ip_list a'):
            onclick = a.get('onclick', '')
            match = re.search(r"filter_ip\('([^']+)'\)", onclick)
            if match:
                ip = match.group(1).strip()
                if ip and ip != '解析失败':
                    ip_list.append(ip)
        return list(set(ip_list))
    except Exception as e:
        # 超时或报错直接安静返回，不打印长串报错干扰屏幕
        return []

def get_ping_data_for_ip(page, ip):
    url = f'https://www.itdog.cn/ping/{ip}'
    try:
        page.goto(url, timeout=TIMEOUT_MS) # 15秒打不开直接抛弃
        page.wait_for_timeout(2000) 
        try:
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
        except: pass
        page.wait_for_timeout(WAIT_TIME) 
        html = page.content()
        soup = BeautifulSoup(html, 'lxml')
        results = []
        for tr in soup.select('tr.node_tr'):
            node = tr.get('node')
            node_type = tr.get('node_type')
            if not node or not node_type: continue
            ping_td = tr.find('td', id=f'ping_{node}')
            latency = ping_td.text.strip() if ping_td else 'N/A'
            results.append({'node_type': node_type, 'latency': latency})
        return results
    except Exception:
        return []

def thread_worker(ip):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        thread_page = context.new_page()
        stealth_sync(thread_page)
        try:
            data = get_ping_data_for_ip(thread_page, ip)
            return ip, data
        except Exception:
            return ip, []
        finally:
            browser.close()

def build_table(provider_name, data_list, top_n=None):
    """动态生成表格，支持全量或 Top N"""
    lines = []
    title = f"Top {top_n}" if top_n else "全量"
    lines.append(f"\n🚀 【{provider_name}】最优 IP 排行 ({title}):")
    lines.append("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    lines.append("| 排名 | IP 地址              | 平均延迟     |")
    lines.append("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    
    if not data_list:
        lines.append("|" + "无有效数据".center(42, " ") + "|")
    else:
        sorted_data = sorted(data_list, key=lambda x: x['avg_latency'])
        if top_n:
            sorted_data = sorted_data[:top_n]
        for idx, item in enumerate(sorted_data, 1):
            ip_str = item['ip'].ljust(20)
            avg_str = f"{item['avg_latency']:.2f} ms".rjust(12)
            lines.append(f"| {idx:<4} | {ip_str} | {avg_str} |")
    lines.append("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    return lines

def main():
    if not os.path.exists('domains.txt'): return
    with open('domains.txt', 'r', encoding='utf-8') as f:
        domains = [line.strip() for line in f if line.strip()]

    print("\n🔎 第一阶段：提取 IP (15秒超时防卡死)...", flush=True)
    all_ips = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        main_page = context.new_page()
        stealth_sync(main_page)
        for domain in domains:
            ips = get_ips_from_domain(main_page, domain)
            all_ips.update(ips)
        browser.close()
    
    final_list = [ip for ip in all_ips if len(ip.split('.')) == 4]
    print(f'\n✅ 获取到 {len(final_list)} 个待测 IP\n', flush=True)
    if not final_list: return

    print(f"📡 第二阶段：开始测速 (线程: {MAX_WORKERS})...", flush=True)
    ip_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(thread_worker, ip): ip for ip in final_list}
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(final_list), desc="🚀 进度", file=sys.stderr, ncols=80):
            ip, data = future.result()
            if data: ip_data[ip] = data

    # 第三阶段：处理数据
    provider_map = {'1': '电信', '2': '联通', '3': '移动', '5': '海外'}
    aggregated_stats = {name: [] for name in provider_map.values()}
    for ip, nodes in ip_data.items():
        grouped_lats = defaultdict(list)
        for n in nodes:
            p_name = provider_map.get(n['node_type'])
            if p_name and 'ms' in n['latency']:
                try: grouped_lats[p_name].append(float(n['latency'].replace('ms', '').strip()))
                except ValueError: continue
        for p_name, lats in grouped_lats.items():
            if lats:
                aggregated_stats[p_name].append({'ip': ip, 'avg_latency': sum(lats)/len(lats)})

    # 第四阶段：分别生成全量数据和 Top5 数据
    full_lines = ['\n' + '=' * 50, '🏆 优选 IP 最终测速报告 (全量存档)', '=' * 50]
    top5_lines = ['\n' + '=' * 50, '🏆 优选 IP 最终测速报告 (四大线路 Top 5)', '=' * 50]

    for p_name in ['电信', '联通', '移动', '海外']:
        full_lines.extend(build_table(p_name, aggregated_stats[p_name], top_n=None))
        top5_lines.extend(build_table(p_name, aggregated_stats[p_name], top_n=5))

    # 将 Top5 打印到屏幕上方便查看
    for line in top5_lines:
        print(line, flush=True)

    # 分别保存为两个文件
    with open('result.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(full_lines) + "\n")
    with open('top5.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(top5_lines) + "\n")
        
    print("\n📝 全量数据已保存至 result.txt，Top5 已保存至 top5.txt", flush=True)

if __name__ == '__main__':
    main()
