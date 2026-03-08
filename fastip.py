import os
import re
import time
import concurrent.futures
from collections import defaultdict
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
from tqdm import tqdm  # 导入进度条库

# --- 配置区 ---
MAX_WORKERS = 3  
WAIT_TIME = 10000  
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get_ips_from_domain(page, domain):
    url = f'https://www.itdog.cn/ping/{domain}'
    try:
        page.goto(url, timeout=60000)
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
        print(f'\n❌ 域名 {domain} 提取异常: {e}', flush=True)
        return []

def get_ping_data_for_ip(page, ip):
    url = f'https://www.itdog.cn/ping/{ip}'
    try:
        page.goto(url, timeout=60000)
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
    except Exception as e:
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
        except:
            return ip, []
        finally:
            browser.close()

def main():
    if not os.path.exists('domains.txt'): return
    with open('domains.txt', 'r', encoding='utf-8') as f:
        domains = [line.strip() for line in f if line.strip()]

    print("\n🔎 第一阶段：提取 IP (实时去重)...", flush=True)
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
    
    final_list = [ip for ip in all_ips if len(ip.split('.')) == 4]
    print(f'\n✅ 最终获取到 {len(final_list)} 个待测 IP\n', flush=True)
    
    if not final_list: return

    # --- 第二阶段：加入进度条控制 ---
    print(f"📡 第二阶段：开始并发测速 (线程数: {MAX_WORKERS})...", flush=True)
    ip_data = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(thread_worker, ip): ip for ip in final_list}
        
        # 使用 tqdm 实时监控，total 设为 IP 总数
        for future in tqdm(concurrent.futures.as_completed(futures), 
                          total=len(final_list), 
                          desc="🚀 整体进度", 
                          ncols=80):
            ip, data = future.result()
            if data:
                ip_data[ip] = data

    # ...（数据处理和打印表格部分保持不变）...
    print("\n🎉 任务圆满完成！", flush=True)

if __name__ == '__main__':
    main()
