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
MAX_WORKERS = 5  # GitHub Actions 建议开 5 个并发，兼顾速度与内存限制
WAIT_TIME = 5000  # 每个 IP 测速等待时间 
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# ==========================================

def get_ips_from_domain(page, domain):
    """从域名页面提取 IP"""
    url = f'https://www.itdog.cn/ping/{domain}'
    try:
        page.goto(url, timeout=60000)
        page.wait_for_timeout(2000) 
        try:
            btn_locator = page.locator("button, input").filter(has_text=re.compile("测试")).first
            btn_locator.click(timeout=3000)
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
        print(f'\n❌ 域名 {domain} 提取异常: {e}', flush=True)
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
        return []

def thread_worker(ip):
    """独立的线程测速任务，自带独立的浏览器实例防止崩溃"""
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

def main():
    if not os.path.exists('domains.txt'):
        print("❌ 错误：找不到 domains.txt", flush=True)
        return
        
    with open('domains.txt', 'r', encoding='utf-8') as f:
        domains = [line.strip() for line in f if line.strip()]

    # ================= 第一阶段：提取 IP =================
    print("\n🔎 第一阶段：提取所有域名包含的 IP...", flush=True)
    all_ips = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        main_page = context.new_page()
        stealth_sync(main_page)
        for domain in domains:
            print(f'  正在解析: {domain}', flush=True)
            ips = get_ips_from_domain(main_page, domain)
            all_ips.update(ips)
        browser.close()
    
    # 严格过滤出合法的 IPv4 格式
    final_list = [ip for ip in all_ips if len(ip.split('.')) == 4]
    print(f'\n✅ 提取完成，共有 {len(final_list)} 个有效 IP 准备进行测试！\n', flush=True)
    
    if not final_list:
        return

    # ================= 第二阶段：并发测速 =================
    print(f"📡 第二阶段：开始并行测速 (线程数: {MAX_WORKERS})...", flush=True)
    ip_data = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(thread_worker, ip): ip for ip in final_list}
        
        for future in tqdm(concurrent.futures.as_completed(futures), 
                           total=len(final_list), 
                           desc="🚀 测速进度", 
                           file=sys.stderr,
                           ncols=80):
            ip, data = future.result()
            if data:
                ip_data[ip] = data

    # ================= 第三阶段：清洗与聚合 =================
    provider_map = {'1': '电信', '2': '联通', '3': '移动', '5': '海外'}
    aggregated_stats = {name: [] for name in provider_map.values()}
    
    for ip, nodes in ip_data.items():
        grouped_lats = defaultdict(list)
        for n in nodes:
            p_name = provider_map.get(n['node_type'])
            if p_name and 'ms' in n['latency']:
                try:
                    grouped_lats[p_name].append(float(n['latency'].replace('ms', '').strip()))
                except ValueError: 
                    continue
        
        for p_name, lats in grouped_lats.items():
            if lats:
                avg = sum(lats) / len(lats)
                aggregated_stats[p_name].append({'ip': ip, 'avg_latency': avg})

    # ================= 第四阶段：成果展示与自动保存 =================
    output_lines = []
    
    def log(msg):
        """同时打印到屏幕并保存到列表"""
        print(msg, flush=True)
        output_lines.append(msg)

    log('\n' + '=' * 50)
    log('🏆 优选 IP 测速报告 (四大线路 Top 5)')
    log('=' * 50)
    
    for p_name in ['电信', '联通', '移动', '海外']:
        data_list = aggregated_stats[p_name]
        log(f"\n🚀 【{p_name}】最优 IP 排行 (Top 5):")
        log("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
        log("| 排名 | IP 地址              | 平均延迟     |")
        log("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")
        
        if not data_list:
            log("|" + "无有效数据".center(42, " ") + "|")
        else:
            top5 = sorted(data_list, key=lambda x: x['avg_latency'])[:5]
            for idx, item in enumerate(top5, 1):
                ip_str = item['ip'].ljust(20)
                avg_str = f"{item['avg_latency']:.2f} ms".rjust(12)
                log(f"| {idx:<4} | {ip_str} | {avg_str} |")
        log("+" + "-"*6 + "+" + "-"*22 + "+" + "-"*14 + "+")

    # 自动把结果写入 result.txt
    try:
        with open('result.txt', 'w', encoding='utf-8') as f:
            f.write("\n".join(output_lines) + "\n")
        print("\n📝 测速报告已自动保存至 result.txt！", flush=True)
    except Exception as e:
        print(f"\n❌ 保存 result.txt 失败: {e}", flush=True)

    print("\n🎉 所有任务圆满结束！", flush=True)

if __name__ == '__main__':
    main()
