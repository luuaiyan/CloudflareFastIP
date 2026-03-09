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
MAX_WORKERS = 3  
WAIT_TIME = 10000
TIMEOUT_MS = 15000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# ==========================================

def get_ipv4_from_domain(page, domain):
    url = f'https://www.itdog.cn/ping/{domain}'
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        page.wait_for_timeout(2000) 
        try: page.locator("button, input").filter(has_text=re.compile("测试")).first.click(timeout=3000)
        except: pass
        page.wait_for_timeout(5000) 
        soup = BeautifulSoup(page.content(), 'lxml')
        ip_list = []
        for a in soup.select('ul.ip_list a'):
            match = re.search(r"filter_ip\('([^']+)'\)", a.get('onclick', ''))
            if match:
                ip = match.group(1).strip()
                if ip and ip != '解析失败': ip_list.append(ip)
        return list(set(ip_list))
    except Exception: return []

def get_ipv6_from_domain(page, domain):
    url = f'https://www.itdog.cn/ping_ipv6/{domain}'
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        page.wait_for_timeout(2000) 
        try: page.locator("button, input").filter(has_text=re.compile("测试")).first.click(timeout=3000)
        except: pass
        page.wait_for_timeout(5000) 
        soup = BeautifulSoup(page.content(), 'lxml')
        ipv6_list = set()
        for tr in soup.select('tr.node_tr'):
            node_id = tr.get('node')
            if node_id:
                ip_div = tr.find('div', id=f'real_ip_{node_id}')
                if ip_div:
                    ip = ip_div.text.strip()
                    if ':' in ip and '失败' not in ip and '等待' not in ip: ipv6_list.add(ip)
        return list(ipv6_list)
    except Exception: return []

def get_ping_data_for_ip(page, ip):
    is_ipv6 = ':' in ip
    url = f'https://www.itdog.cn/ping_ipv6/{ip}' if is_ipv6 else f'https://www.itdog.cn/ping/{ip}'
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        page.wait_for_timeout(2000) 
        try: page.locator("button, input").filter(has_text=re.compile("测试")).first.click(timeout=3000)
        except: pass
        page.wait_for_timeout(WAIT_TIME) 
        soup = BeautifulSoup(page.content(), 'lxml')
        results = []
        for tr in soup.select('tr.node_tr'):
            node = tr.get('node')
            node_type = tr.get('node_type')
            if not node or not node_type: continue
            ping_td = tr.find('td', id=f'ping_{node}')
            latency = ping_td.text.strip() if ping_td else 'N/A'
            results.append({'node_type': node_type, 'latency': latency})
        return results
    except Exception: return []

def thread_worker(ip):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        thread_page = context.new_page()
        stealth_sync(thread_page)
        try:
            data = get_ping_data_for_ip(thread_page, ip)
            return ip, data
        except Exception: return ip, []
        finally: browser.close()

def build_table(provider_name, data_list, top_n=None):
    lines = []
    title = f"Top {top_n}" if top_n else "全量"
    lines.append(f"\n🚀 【{provider_name}】最优 IP 排行 ({title}):")
    lines.append("+" + "-"*6 + "+" + "-"*41 + "+" + "-"*14 + "+")
    lines.append("| 排名 | IP 地址" + " "*32 + " | 平均延迟     |")
    lines.append("+" + "-"*6 + "+" + "-"*41 + "+" + "-"*14 + "+")
    
    if not data_list:
        lines.append("|" + "无有效数据".center(61, " ") + "|")
    else:
        sorted_data = sorted(data_list, key=lambda x: x['avg_latency'])
        if top_n: sorted_data = sorted_data[:top_n]
        for idx, item in enumerate(sorted_data, 1):
            ip_str = item['ip'].ljust(39)
            avg_str = f"{item['avg_latency']:.2f} ms".rjust(12)
            lines.append(f"| {idx:<4} | {ip_str} | {avg_str} |")
    lines.append("+" + "-"*6 + "+" + "-"*41 + "+" + "-"*14 + "+")
    return lines

def main():
    if not os.path.exists('domains.txt'): return
    with open('domains.txt', 'r', encoding='utf-8') as f:
        domains = [line.strip() for line in f if line.strip()]

    print("\n🔎 第一阶段：双栈提取 IPv4 & IPv6...", flush=True)
    all_ips = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        main_page = context.new_page()
        stealth_sync(main_page)
        for domain in domains:
            ipv4s = get_ipv4_from_domain(main_page, domain)
            ipv6s = get_ipv6_from_domain(main_page, domain)
            all_ips.update(ipv4s)
            all_ips.update(ipv6s)
        browser.close()
    
    final_list = [ip for ip in all_ips if len(ip.split('.')) == 4 or ':' in ip]
    v4_count = sum(1 for ip in final_list if ':' not in ip)
    v6_count = sum(1 for ip in final_list if ':' in ip)
    print(f'\n✅ 提取完成！共计 {len(final_list)} 个 IP (IPv4: {v4_count}, IPv6: {v6_count})\n', flush=True)
    if not final_list: return

    print(f"📡 第二阶段：双栈智能测速 (线程: {MAX_WORKERS})...", flush=True)
    ip_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(thread_worker, ip): ip for ip in final_list}
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(final_list), desc="🚀 进度", file=sys.stderr, ncols=80):
            ip, data = future.result()
            if data: ip_data[ip] = data

    # 第三阶段：处理数据并按 IPv4 / IPv6 拆分
    provider_map = {'1': '电信', '2': '联通', '3': '移动', '5': '海外'}
    
    # 建立两个独立的统计字典
    v4_stats = {name: [] for name in provider_map.values()}
    v6_stats = {name: [] for name in provider_map.values()}
    
    for ip, nodes in ip_data.items():
        grouped_lats = defaultdict(list)
        for n in nodes:
            p_name = provider_map.get(n['node_type'])
            if p_name and 'ms' in n['latency']:
                try: grouped_lats[p_name].append(float(n['latency'].replace('ms', '').strip()))
                except ValueError: continue
        
        for p_name, lats in grouped_lats.items():
            if lats:
                avg_lat = sum(lats)/len(lats)
                # 根据 IP 类型分流
                if ':' in ip:
                    v6_stats[p_name].append({'ip': ip, 'avg_latency': avg_lat})
                else:
                    v4_stats[p_name].append({'ip': ip, 'avg_latency': avg_lat})

    # 第四阶段：生成三大文件的内容
    full_lines = ['\n' + '=' * 65, '🏆 优选 IP 最终测速报告 (双栈全量存档)', '=' * 65]
    v4_top5_lines = ['\n' + '=' * 65, '🟢 优选 IPv4 最终测速报告 (四大线路 Top 5)', '=' * 65]
    v6_top5_lines = ['\n' + '=' * 65, '🔵 优选 IPv6 最终测速报告 (四大线路 Top 5)', '=' * 65]

    for p_name in ['电信', '联通', '移动', '海外']:
        # 1. 组装全量报告 (为了可读性，全量报告里也把 v4 和 v6 分开展示)
        full_lines.extend(build_table(f"{p_name} - IPv4", v4_stats[p_name], top_n=None))
        full_lines.extend(build_table(f"{p_name} - IPv6", v6_stats[p_name], top_n=None))
        
        # 2. 组装独立的 Top 5 报告
        v4_top5_lines.extend(build_table(p_name, v4_stats[p_name], top_n=5))
        v6_top5_lines.extend(build_table(p_name, v6_stats[p_name], top_n=5))

    # 打印到控制台
    for line in v4_top5_lines + v6_top5_lines:
        print(line, flush=True)

    # 写入三个独立文件
    with open('result.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(full_lines) + "\n")
    with open('ipv4-top5.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(v4_top5_lines) + "\n")
    with open('ipv6-top5.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(v6_top5_lines) + "\n")
        
    print("\n📝 成功生成 3 个文件: result.txt, ipv4-top5.txt, ipv6-top5.txt", flush=True)

if __name__ == '__main__':
    main()
