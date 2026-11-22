# cfip 合并版：一次抓取，分写三个文件
# 一个脚本代替原来的 main.py / yd.py / dx.py：
#   只用一个无头浏览器加载一次原网页，从同一次渲染结果里提取全部四组 IP，
#   再分写 public/all.txt（全量）、public/ip.txt（移动）、public/dx.txt（电信+多线）。
#   三个文件要么一起更新、要么一个都不更新，再也不会出现 all.txt 比另外两个旧、
#   或者三个文件不是同一批数据的情况。
#
# 失败时行为（特意改的）：
#   抓取失败、提取不到任一组、或四组里有任何一组为空 -> 直接报错退出（非零退出码），
#   一个文件也不写。workflow 里不再加 `|| true`，任务会变红、不会提交半成品。
#   以前 main.py 失败会被 `|| true` 藏起来，任务还显示 Success，就是 all.txt
#   几次没更新的原因。另外旧 main.py 用的 ±60 字符滑动窗口在这一页上本来就
#   不够用（运营商和 IP 之间隔了 60~70 个字符），现在统一换成按表格行提取。
#
# 格式注意：多线那 10 行必须写成“IP:端口#CF 优选”，不能写成“#CF 多线优选”——
# v13 Worker 的 GitHub 解析正则只认 电信/联通/移动 三个词，没标签的默认按
# 多线算；写成“#CF 多线优选”反而会被 v13 整个丢掉。
import re
import os
import sys
from playwright.sync_api import sync_playwright

LABELS = {
    "电信": "CF 电信优选",
    "联通": "CF 联通优选",
    "移动": "CF 移动优选",
    "多线": "CF 优选",  # 无标签 = 多线，见上方说明
}
CARRIER_ORDER = ["电信", "联通", "移动", "多线"]

IP_PATTERN = r'\b((?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))\b'
PORT_PATTERN = r'\b(80|443|8080|8443|2052|2053|2082|2083|2086|2087|2095|2096)\b'
CARRIER_PATTERN = r'电信|联通|移动|多线'
TR_PATTERN = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S | re.I)


def extract_all_ips(html_text):
    """从渲染后的 HTML 里提取全部四组 IP。
    返回 {"电信": [...], "联通": [...], "移动": [...], "多线": [...]}，
    每条格式为 'IP:端口#CF xx优选'。"""
    seen = set()
    groups = {c: [] for c in CARRIER_ORDER}

    def add(ip, port, carrier):
        if ip in seen or ip.startswith('127.') or ip.startswith('0.'):
            return
        seen.add(ip)
        groups[carrier].append(f"{ip}:{port}#{LABELS[carrier]}")

    # 1) 按表格行解析：同一 <tr> 里的运营商和 IP 一一对应，最稳。
    #    原版 ±60 字符的滑动窗口在这一页上不够用：运营商单元格和 IP 单元格
    #    之间隔了 60~70 个字符的换行和缩进，窗口小了会认不出运营商。
    #    多线组在行里其实带“多线”标签，这里能认出来；但输出时仍写成
    #    “#CF 优选”（见文件头说明），因为 v13 的 GitHub 正则不认“多线”二字。
    for tr in TR_PATTERN.finditer(html_text):
        body = tr.group(1)
        m = re.search(CARRIER_PATTERN, body)
        carrier = m.group(0) if m else "多线"  # 没有运营商标签的行按多线算
        for ip in re.findall(IP_PATTERN, body):
            port_m = re.search(PORT_PATTERN, body)
            add(ip, port_m.group(1) if port_m else "443", carrier)
    if any(groups.values()):
        return groups

    # 2) 兜底：滑动窗口（窗口放宽到 200 字符）。每个 IP 取窗口内离它最近的
    #    运营商标签；窗口里一个标签都没有的按多线算。
    for match in re.finditer(IP_PATTERN, html_text):
        ip = match.group(1)
        if ip in seen or ip.startswith('127.') or ip.startswith('0.'):
            continue
        start = max(0, match.start() - 200)
        end = min(len(html_text), match.end() + 200)
        context_str = html_text[start:end]
        best = None
        best_dist = None
        for cm in re.finditer(CARRIER_PATTERN, context_str):
            dist = abs(cm.start() - (match.start() - start))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = cm.group(0)
        carrier = best if best else "多线"
        port_match = re.search(PORT_PATTERN, context_str)
        add(ip, port_match.group(1) if port_match else "443", carrier)
    return groups


def main():
    url = 'https://api.uouin.com/cloudflare.html'

    print("🚀 一次抓取，分写三个文件：真实无头浏览器加载原网页...")
    with sync_playwright() as p:
        # 启动 Chrome 内核（无头模式）
        browser = p.chromium.launch(headless=True)
        # 伪装正常用户的分辨率和指纹
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        try:
            print(f"1. 正在控制真实浏览器访问页面: {url}")
            # 打开网页，等待网络连接闲置
            page.goto(url, wait_until='networkidle', timeout=30000)

            # 强行等待 5 秒钟！
            # 给网页里的 JS 留出充足的时间，让它自己去算密钥、请求接口、渲染 IP 列表
            print("2. 网页加载完毕，等待 5 秒钟让其动态渲染数据...")
            page.wait_for_timeout(5000)

            # 获取网页渲染完成后的最终完整 HTML（此时 IP 已经显示在页面上了）
            html_text = page.content()
            title = page.title()
            print(f"-> 获取到最终渲染网页，标题为: {title}，HTML 长度: {len(html_text)}")
        finally:
            browser.close()

    # 下面不再吞异常：任何一步失败都直接让进程非零退出，GitHub 任务变红，
    # 并且一个文件也不写，避免三个文件不同步。
    print("3. 开始从同一次渲染结果里提取四组 IP...")
    groups = extract_all_ips(html_text)
    counts = {c: len(groups[c]) for c in CARRIER_ORDER}
    print(f"-> 提取结果：电信 {counts['电信']}，联通 {counts['联通']}，"
          f"移动 {counts['移动']}，多线 {counts['多线']}")

    missing = [c for c in CARRIER_ORDER if not groups[c]]
    if missing:
        print(f"❌ 以下线路一条都没提取到：{'、'.join(missing)}，不写文件，直接报错退出。")
        print("-> 页面前500字符诊断:", html_text[:500])
        sys.exit(1)

    all_lines = groups["电信"] + groups["联通"] + groups["移动"] + groups["多线"]
    mobile_lines = groups["移动"]
    dx_lines = groups["电信"] + groups["多线"]

    os.makedirs('public', exist_ok=True)
    with open('public/all.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(all_lines))
    with open('public/ip.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(mobile_lines))
    with open('public/dx.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(dx_lines))
    print(f"🎉 一次抓取完成：all.txt {len(all_lines)} 条，ip.txt {len(mobile_lines)} 条，"
          f"dx.txt {len(dx_lines)} 条，三个文件是同一批数据。")

if __name__ == '__main__':
    main()
