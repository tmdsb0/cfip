# cf-ip-page 电信 + 多线专用版：只抓取电信（CTCC）和多线（BGP）的优选 IP
# 与原版的区别：提取时只保留运营商为“电信”的行和无标签的行（原页面第 4 个
# IPv4 分组就是多线），联通、移动的行一律丢掉；输出是 public/dx.txt。
#
# 注意：多线那 10 行必须写成“IP:端口#CF 优选”，不能写成“#CF 多线优选”——
# v13 Worker 的 GitHub 解析正则只认 电信/联通/移动 三个词，没标签的默认按
# 多线算；写成“#CF 多线优选”反而会被 v13 整个丢掉。
import re
import os
import time
from playwright.sync_api import sync_playwright

TELECOM_LABEL = "CF 电信优选"
BGP_LABEL = "CF 优选"  # 无标签 = 多线，见上方说明

IP_PATTERN = r'\b((?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))\b'
PORT_PATTERN = r'\b(80|443|8080|8443|2052|2053|2082|2083|2086|2087|2095|2096)\b'
CARRIER_PATTERN = r'电信|联通|移动|多线'
TR_PATTERN = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S | re.I)


def extract_telecom_bgp_ips(html_text):
    """从渲染后的 HTML 里只提取电信和多线的 IP，
    返回 ['IP:端口#CF 电信优选', 'IP:端口#CF 优选', ...]"""
    seen = set()
    results = []

    def add(ip, port, label):
        if ip in seen or ip.startswith('127.') or ip.startswith('0.'):
            return
        seen.add(ip)
        results.append(f"{ip}:{port}#{label}")

    # 1) 按表格行解析：同一 <tr> 里的运营商和 IP 一一对应，最稳。
    #    原版 ±60 字符的滑动窗口在这一页上不够用：运营商单元格和 IP 单元格
    #    之间隔了 60~70 个字符的换行和缩进，窗口小了会认不出运营商。
    #    注意：多线组在行里其实带“多线”标签，这里能认出来；但输出时仍写
    #    成“#CF 优选”（见文件头说明），因为 v13 的 GitHub 正则不认“多线”二字。
    for tr in TR_PATTERN.finditer(html_text):
        body = tr.group(1)
        m = re.search(CARRIER_PATTERN, body)
        if m:
            if m.group(0) == "电信":
                label = TELECOM_LABEL
            elif m.group(0) == "多线":
                label = BGP_LABEL
            else:
                continue
        else:
            # 没有运营商标签的行也按多线算（与 v13 的做法一致）
            label = BGP_LABEL
        for ip in re.findall(IP_PATTERN, body):
            port_m = re.search(PORT_PATTERN, body)
            add(ip, port_m.group(1) if port_m else "443", label)
    if results:
        return results

    # 2) 兜底：滑动窗口（窗口放宽到 200 字符；上下文里出现联通/移动的不要，
    #    避免隔壁行的标签串进来造成误判；什么标签都没有的按多线算）
    for match in re.finditer(IP_PATTERN, html_text):
        ip = match.group(1)
        if ip in seen or ip.startswith('127.') or ip.startswith('0.'):
            continue
        start = max(0, match.start() - 200)
        end = min(len(html_text), match.end() + 200)
        context_str = html_text[start:end]
        if "联通" in context_str or "移动" in context_str:
            continue
        if "电信" in context_str:
            label = TELECOM_LABEL
        else:
            label = BGP_LABEL
        port_match = re.search(PORT_PATTERN, context_str)
        add(ip, port_match.group(1) if port_match else "443", label)
    return results


def main():
    url = 'https://api.uouin.com/cloudflare.html'

    print("🚀 启动物理降维打击：真实无头浏览器加载模式（只抓电信 + 多线）...")
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
            print(f"-> 获取到最终渲染网页，标题为: {title}")

            print("3. 开始按表格行提取电信和多线的 IP 数据...")
            results = extract_telecom_bgp_ips(html_text)

            if results:
                os.makedirs('public', exist_ok=True)
                with open('public/dx.txt', 'w', encoding='utf-8') as f:
                    f.write('\n'.join(results))
                print(f"🎉 物理降维打击成功！彻底无视接口加密，成功提取 {len(results)} 个最新电信 + 多线 IP！")
            else:
                print("⚠️ 页面已经渲染完毕，但未能提取到电信/多线 IP。这通常说明网站彻底改版，或者出现了极高强度的真人验证码弹窗。")
                print("-> 页面前500字符诊断:", html_text[:500])

        except Exception as e:
            print(f"抓取发生错误: {e}")
        finally:
            browser.close()

if __name__ == '__main__':
    main()
