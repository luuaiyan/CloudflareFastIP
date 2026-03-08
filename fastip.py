import os
import re
import time
import concurrent.futures
from collections import defaultdict
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

# --- 配置区 ---
MAX_WORKERS = 3  # 并发线程数，本地运行建议 2-3，防止 CPU 飙升
WAIT_TIME = 10000  # 每个 IP 测速等待时间 (10秒)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get_ips_from_domain(page, domain):
    """从域名页面提取 IP"""
    url = f'https://www.itdog.cn/ping/{domain}'
    try:
        page.goto(url, timeout=60000)
        page.wait_for_timeout(2000) 
        try:
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
            print(f"  👆 成功触发域名 {domain} 的测试！")
        except:
            pass
            
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
        print(f'❌ 域名 {domain} 提取异常: {e}')
        return []

def get_ping_data_for_ip(page, ip):
    """测试单个 IP 的延迟数据"""
    url = f'https://www.itdog.cn/ping/{ip}'
    try:
        page.goto(url, timeout=60000)
        page.wait_for_timeout(2000) 
        try:
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
        except:
            pass
            
        page.wait_for_timeout(WAIT_TIME) 
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
            results.append({'node_type': node_type, 'latency': latency})
        return results
    except Exception as e:
        print(f'❌ IP {ip} 测速异常: {e}')
        return []

def thread_worker(ip):
    """线程工作函数：每个线程拥有完全独立的 Playwright 实例"""
    with sync_playwright() as p:
        # 针对每个 IP 启动独立的浏览器进程，彻底规避线程竞争
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        thread_page = context.new_page()
        stealth_sync(thread_page)
        try:
            print(f'  🚀 并行测速中: {ip}')
            data = get_ping_data_for_ip(thread_page, ip)
            return ip, data
        except Exception as e:
            print(f"  ⚠️ IP {ip} 测速失败: {e}")
            return ip, []
        finally:
            browser.close()

def print_top5_table(provider_name, data_list):
    """打印 ASCII 表格"""
    print(f"\n🚀 【{provider_name}】最优 IP 排行 (Top 5):")
    print("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    print("| 排名 | IP 地址              | 平均延迟     |")
    print("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
    if not data_list:
        print("|" + "无有效数据".center(42, " ") + "|")
    else:
        top5 = sorted(data_list, key=lambda x: x['avg_latency'])[:5]
        for idx, item in enumerate(top5, 1):
            ip_str = item['ip'].ljust(20)
            avg_str = f"{item['avg_latency']:.2f} ms".rjust(12)
            print(f"| {idx:<4} | {ip_str} | {avg_str} |")
    print("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")

def main():
    if not os.path.exists('domains.txt'):
        print("❌ 错误：找不到 domains.txt")
        return
    with open('domains.txt', 'r', encoding='utf-8') as f:
        domains = [line.strip() for line in f if line.strip()]

    # 1. 第一阶段：单线程提取 IP (ITDog 首页对并发敏感，串行更稳)
    print("\n🔎 第一阶段：提取 IP (实时去重)...")
    all_ips = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        main_page = context.new_page()
        stealth_sync(main_page)
        for domain in domains:
            print(f'  正在解析: {domain}')
            ips = get_ips_from_domain(main_page, domain)
            new_found = [ip for ip in ips if ip not in all_ips]
            if new_found:
                print(f"    ✨ 发现 {len(new_found)} 个新 IP")
                all_ips.update(new_found)
            time.sleep(1)
        browser.close()
    
    final_list = [ip for ip in all_ips if len(ip.split('.')) == 4]
    print(f'\n✅ 最终获取到 {len(final_list)} 个待测唯一 IP\n')
    
    if not final_list:
        print("🛑 无有效 IP，退出。")
        return

    # 2. 第二阶段：多线程并发测速
    print(f"📡 第二阶段：开始并发测速 (线程数: {MAX_WORKERS})...")
    ip_data = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 将每个 IP 作为一个独立任务提交
        future_to_ip = {executor.submit(thread_worker, ip): ip for ip in final_list}
        for future in concurrent.futures.as_completed(future_to_ip):
            ip, data = future.result()
            if data:
                ip_data[ip] = data

    # 3. 数据清洗与排行
    provider_map = {'1': '电信', '2': '联通', '3': '移动', '5': '海外'}
    aggregated_stats = {name: [] for name in provider_map.values()}
    
    for ip, nodes in ip_data.items():
        grouped_lats = defaultdict(list)
        for n in nodes:
            p_name = provider_map.get(n['node_type'])
            if p_name and 'ms' in n['latency']:
                try:
                    grouped_lats[p_name].append(float(n['latency'].replace('ms', '').strip()))
                except: continue
        
        for p_name, lats in grouped_lats.items():
            if lats:
                avg = sum(lats) / len(lats)
                aggregated_stats[p_name].append({'ip': ip, 'avg_latency': avg})

    # 4. 终端输出结果
    print('\n' + '=' * 50)
    print('🏆 优选 IP 最终测速报告 (四大线路 Top 5)')
    print('=' * 50)
    for p_name in ['电信', '联通', '移动', '海外']:
        print_top5_table(p_name, aggregated_stats[p_name])
    
    print("\n🎉 任务圆满完成！")

if __name__ == '__main__':
    main()
