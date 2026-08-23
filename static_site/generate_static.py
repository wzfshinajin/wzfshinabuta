#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MediaWiki 静态化脚本
=====================
将本地 MediaWiki 站点转换为可在 GitHub Pages 上托管的静态站点。

功能：
1. 通过 MediaWiki API 获取所有页面列表
2. 抓取每个页面的完整渲染 HTML
3. 下载所有引用的静态资源（CSS、JS、图片）
4. 重写链接为相对路径，页面间链接指向 .html 文件
5. 保留动态元素但使其无作用（移除表单提交、登录等）
6. 生成 GitHub Pages 兼容的目录结构

用法：
    python generate_static.py
"""

import os
import re
import sys
import json
import shutil
import hashlib
import html as html_lib
import urllib.parse
import urllib.request
from pathlib import Path

# ============================================================
# 配置
# ============================================================

# MediaWiki 站点地址
BASE_URL = "http://localhost:8888"
SCRIPT_PATH = "/Gokouwiki"
API_URL = f"{BASE_URL}{SCRIPT_PATH}/api.php"

# 输出目录（相对于脚本所在目录）
OUTPUT_DIR = Path(__file__).parent / "site"

# 站点名称
SITE_NAME = "伍寇维基"

# 需要抓取的命名空间（0=主, 10=模板）
# 注意：文件(6)和分类(14)命名空间不需要抓取为页面
NAMESPACES = [0, 10]

# 需要跳过的页面前缀
SKIP_PREFIXES = [
    "特殊:",
    "Special:",
    "MediaWiki:",
]

# ============================================================
# 工具函数
# ============================================================

def url_encode_path(path: str) -> str:
    """对 URL 路径进行编码，保留 / 和 ."""
    return urllib.parse.quote(path, safe="/.~")

def safe_filename(name: str) -> str:
    """将页面标题转换为安全的文件名"""
    # 替换 Windows 不允许的字符
    name = name.replace("/", "_").replace("\\", "_")
    name = name.replace(":", "_").replace("*", "_")
    name = name.replace("?", "_").replace('"', "_")
    name = name.replace("<", "_").replace(">", "_")
    name = name.replace("|", "_")
    return name

def http_get(url: str, timeout: int = 60) -> bytes:
    """发送 GET 请求并返回响应内容"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MediaWikiStaticGenerator/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def http_get_text(url: str, timeout: int = 60) -> str:
    """发送 GET 请求并返回文本内容"""
    data = http_get(url, timeout)
    # 尝试多种编码
    for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")

def download_file(url: str, dest: Path) -> bool:
    """下载文件到指定路径，返回是否成功"""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = http_get(url)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"  [警告] 下载失败: {url} -> {e}")
        return False

def decode_html_entities(s: str) -> str:
    """解码 HTML 实体"""
    return html_lib.unescape(s)

# ============================================================
# 页面抓取
# ============================================================

def get_all_pages() -> list:
    """通过 API 获取所有页面列表"""
    pages = []
    for ns in NAMESPACES:
        apfrom = ""
        while True:
            url = f"{API_URL}?action=query&list=allpages&aplimit=500&format=json&apnamespace={ns}"
            if apfrom:
                url += f"&apfrom={urllib.parse.quote(apfrom)}"
            try:
                data = json.loads(http_get_text(url))
            except Exception as e:
                print(f"  [错误] 获取命名空间 {ns} 页面失败: {e}")
                break

            allpages = data.get("query", {}).get("allpages", [])
            if not allpages:
                break

            for page in allpages:
                pages.append({
                    "pageid": page["pageid"],
                    "ns": page["ns"],
                    "title": page["title"],
                })

            # 检查是否有更多页面
            if "continue" in data:
                apfrom = data["continue"].get("apfrom", "")
            else:
                break

    return pages

def get_page_html(title: str) -> str:
    """抓取页面的完整渲染 HTML"""
    url = f"{BASE_URL}{SCRIPT_PATH}/index.php?title={urllib.parse.quote(title)}"
    try:
        return http_get_text(url)
    except Exception as e:
        print(f"  [错误] 抓取页面 {title} 失败: {e}")
        return None

# ============================================================
# 资源下载
# ============================================================

class ResourceCollector:
    """收集并下载页面中引用的所有资源"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.resources_dir = output_dir / "resources"
        self.images_dir = output_dir / "images"
        self.downloaded = {}  # url -> local_path
        self.css_urls = set()
        self.js_urls = set()

    def collect_from_html(self, html: str) -> str:
        """从 HTML 中提取并下载资源，返回重写后的 HTML"""
        # 收集 CSS 链接
        for m in re.finditer(r'<link[^>]*rel=["\']stylesheet["\'][^>]*>', html):
            href_m = re.search(r'href=["\']([^"\']+)["\']', m.group(0))
            if href_m:
                url = decode_html_entities(href_m.group(1))
                self.css_urls.add(url)

        # 收集 JS 链接
        for m in re.finditer(r'<script[^>]*src=["\']([^"\']+)["\'][^>]*>', html):
            url = decode_html_entities(m.group(1))
            self.js_urls.add(url)

        return html

    def download_all(self):
        """下载所有收集到的资源"""
        print("\n[3/5] 下载 CSS 资源...")
        for url in sorted(self.css_urls):
            self._download_resource(url, "css")

        print("\n[4/5] 下载 JS 资源...")
        for url in sorted(self.js_urls):
            self._download_resource(url, "js")

    def _download_resource(self, url: str, kind: str):
        """下载单个资源文件"""
        if url in self.downloaded:
            return

        # 解析 URL
        if url.startswith("http"):
            full_url = url
        elif url.startswith("/"):
            full_url = f"{BASE_URL}{url}"
        else:
            full_url = f"{BASE_URL}{SCRIPT_PATH}/{url}"

        # 生成本地文件名
        parsed = urllib.parse.urlparse(full_url)
        path = parsed.path
        query = parsed.query

        # 对于 load.php 生成的资源，使用哈希命名
        if "load.php" in path:
            hash_str = hashlib.md5(full_url.encode()).hexdigest()[:12]
            ext = ".css" if kind == "css" else ".js"
            local_name = f"{hash_str}{ext}"
        else:
            # 使用原始路径的文件名
            filename = os.path.basename(path)
            if not filename:
                filename = f"resource_{hashlib.md5(full_url.encode()).hexdigest()[:8]}"
            local_name = filename

        local_path = self.resources_dir / kind / local_name
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if download_file(full_url, local_path):
            self.downloaded[url] = f"resources/{kind}/{local_name}"
            print(f"  ✓ {local_name} ({local_path.stat().st_size} bytes)")

            # 如果是 CSS，检查其中引用的图片
            if kind == "css":
                self._download_css_images(local_path, full_url)

            # 如果是 JS，移除动态加载模块的调用（静态站点无法加载）
            if kind == "js":
                self._neutralize_js(local_path)

    def _neutralize_js(self, js_path: Path):
        """移除 JS 中会尝试动态加载模块的调用"""
        try:
            content = js_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        original = content
        # 将 mw.loader.load(window.RLPAGEMODULES||[]) 替换为空操作
        content = re.sub(
            r'mw\.loader\.load\(window\.RLPAGEMODULES\|\|\[\]\)',
            'mw.loader.load=function(){}',
            content
        )
        # 移除 mw.loader.impl 调用（这些是模块定义，静态站点不需要）
        # 注意：保留 mw.loader.state 和 mw.config.set，它们只是设置数据

        if content != original:
            js_path.write_text(content, encoding="utf-8")
            print(f"  ✓ 已中和 JS 动态加载: {js_path.name}")

    def _download_css_images(self, css_path: Path, css_url: str):
        """下载 CSS 中引用的图片"""
        try:
            css_content = css_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        # 查找 url(...) 引用
        for m in re.finditer(r'url\(["\']?([^"\'\)]+)["\']?\)', css_content):
            img_url = m.group(1)
            if img_url.startswith("data:"):
                continue
            if img_url.startswith("#"):
                continue

            # 解析相对路径
            if img_url.startswith("http"):
                full_img_url = img_url
            elif img_url.startswith("/"):
                full_img_url = f"{BASE_URL}{img_url}"
            else:
                # 相对于 CSS 文件的 URL
                css_dir = os.path.dirname(css_url)
                full_img_url = f"{css_dir}/{img_url}"

            # 下载图片
            local_img = self.download_image(full_img_url)
            if local_img:
                # 重写 CSS 中的引用
                new_css = css_content.replace(m.group(0), f'url("{local_img}")')
                css_path.write_text(new_css, encoding="utf-8")

    def download_image(self, url: str) -> str:
        """下载图片并返回本地路径"""
        if url in self.downloaded:
            return self.downloaded[url]

        # 解析 URL
        if url.startswith("http"):
            full_url = url
        elif url.startswith("/"):
            full_url = f"{BASE_URL}{url}"
        else:
            full_url = f"{BASE_URL}{SCRIPT_PATH}/{url}"

        # 生成本地文件名
        parsed = urllib.parse.urlparse(full_url)
        path = parsed.path

        # 提取文件名
        filename = os.path.basename(path)
        if not filename:
            filename = f"img_{hashlib.md5(full_url.encode()).hexdigest()[:8]}"

        # 处理 URL 编码的文件名
        try:
            filename = urllib.parse.unquote(filename)
        except Exception:
            pass

        # 安全文件名
        filename = safe_filename(filename)

        # 对于缩略图，保留原始文件名
        local_path = self.images_dir / filename
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if download_file(full_url, local_path):
            self.downloaded[url] = f"images/{filename}"
            return f"images/{filename}"
        return None

# ============================================================
# HTML 处理
# ============================================================

class HTMLRewriter:
    """重写 HTML 中的链接和资源引用"""

    def __init__(self, collector: ResourceCollector, page_map: dict, output_dir: Path):
        self.collector = collector
        self.page_map = page_map  # title -> filename
        self.output_dir = output_dir

    def rewrite(self, html: str, current_page: str) -> str:
        """重写 HTML 中的所有链接和资源引用"""
        # 1. 重写 CSS 链接
        html = self._rewrite_css_links(html)

        # 2. 重写 JS 链接
        html = self._rewrite_js_links(html)

        # 3. 重写图片链接
        html = self._rewrite_images(html)

        # 4. 重写页面链接
        html = self._rewrite_page_links(html, current_page)

        # 5. 移除动态元素
        html = self._neutralize_dynamic(html)

        return html

    def _rewrite_css_links(self, html: str) -> str:
        """重写 CSS 链接"""
        def replace(m):
            tag = m.group(0)
            href_m = re.search(r'href=["\']([^"\']+)["\']', tag)
            if not href_m:
                return tag
            url = decode_html_entities(href_m.group(1))
            if url in self.collector.downloaded:
                local = self.collector.downloaded[url]
                return tag.replace(href_m.group(0), f'href="{local}"')
            return tag
        return re.sub(r'<link[^>]*rel=["\']stylesheet["\'][^>]*>', replace, html)

    def _rewrite_js_links(self, html: str) -> str:
        """重写 JS 链接"""
        def replace(m):
            tag = m.group(0)
            src_m = re.search(r'src=["\']([^"\']+)["\']', tag)
            if not src_m:
                return tag
            url = decode_html_entities(src_m.group(1))
            if url in self.collector.downloaded:
                local = self.collector.downloaded[url]
                return tag.replace(src_m.group(0), f'src="{local}"')
            return tag
        return re.sub(r'<script[^>]*src=["\'][^"\']+["\'][^>]*>', replace, html)

    def _rewrite_images(self, html: str) -> str:
        """重写图片链接"""
        def replace(m):
            tag = m.group(0)
            src_m = re.search(r'src=["\']([^"\']+)["\']', tag)
            if not src_m:
                return tag
            url = decode_html_entities(src_m.group(1))

            # 处理本地图片（/Gokouwiki/images/ 和 /Gokouwiki/resources/ 路径）
            if url.startswith("/Gokouwiki/images/") or "/Gokouwiki/images/" in url or \
               url.startswith("/Gokouwiki/resources/") or "/Gokouwiki/resources/" in url:
                local = self.collector.download_image(url)
                if local:
                    # 移除 srcset 属性（避免引用不存在的资源）
                    tag = re.sub(r'\s+srcset=["\'][^"\']*["\']', '', tag)
                    return tag.replace(src_m.group(0), f'src="{local}"')
            return tag
        return re.sub(r'<img[^>]*>', replace, html)

    def _rewrite_page_links(self, html: str, current_page: str) -> str:
        """重写页面链接"""
        # 处理 /Gokouwiki/index.php/页面名 格式的链接
        def replace_index(m):
            href = decode_html_entities(m.group(1))
            # 解码标题
            title = urllib.parse.unquote(href)
            # 去掉前缀
            if title.startswith("/Gokouwiki/index.php/"):
                title = title[len("/Gokouwiki/index.php/"):]
            elif title.startswith("index.php/"):
                title = title[len("index.php/"):]
            else:
                return m.group(0)

            # 查找对应的静态文件
            if title in self.page_map:
                target = self.page_map[title]
                return f'href="{target}"'
            # 不在页面列表中的链接改为无作用
            return 'href="#"'

        html = re.sub(r'href="(/Gokouwiki/index\.php/[^"]*)"', replace_index, html)

        # 处理 /Gokouwiki/index.php?title=页面名 格式的链接
        def replace_query(m):
            href = decode_html_entities(m.group(1))
            parsed = urllib.parse.urlparse(href)
            query = urllib.parse.parse_qs(parsed.query)

            if "title" in query:
                title = query["title"][0]
                action = query.get("action", ["view"])[0]

                # 只处理查看操作
                if action == "view" and title in self.page_map:
                    return f'href="{self.page_map[title]}"'

                # 其他操作（编辑、历史等）保留但改为无作用
                return f'href="#"'

            return m.group(0)

        html = re.sub(r'href="(/Gokouwiki/index\.php\?[^"]*)"', replace_query, html)

        # 处理特殊页面链接（保留但无作用）
        html = re.sub(
            r'href="(/Gokouwiki/(?:index\.php\?title=)?(?:特殊|Special):[^"]*)"',
            r'href="#"',
            html
        )

        # 处理 rest.php 链接
        html = re.sub(r'href="(/Gokouwiki/rest\.php[^"]*)"', r'href="#"', html)
        html = re.sub(r'href="(/Gokouwiki/api\.php[^"]*)"', r'href="#"', html)

        # 处理绝对 URL 链接（http://localhost:8888/Gokouwiki/...）
        html = re.sub(
            r'href="(?:http://localhost:8888)?/Gokouwiki/index\.php\?title=([^"]*)"',
            lambda m: self._handle_absolute_query(m.group(1)),
            html
        )

        return html

    def _handle_absolute_query(self, title_encoded: str) -> str:
        """处理绝对 URL 的查询参数链接"""
        title = urllib.parse.unquote(title_encoded)
        # 去掉 action 参数
        if "&" in title:
            title = title.split("&")[0]
        if title in self.page_map:
            return f'href="{self.page_map[title]}"'
        return 'href="#"'

    def _neutralize_dynamic(self, html: str) -> str:
        """使动态元素保留但无作用"""
        # 1. 移除表单的 action，防止提交
        html = re.sub(r'<form([^>]*)action=["\'][^"\']*["\']', r'<form\1action="#"', html)

        # 2. 移除 RSD 和 EditURI 链接
        html = re.sub(r'<link[^>]*rel=["\']EditURI["\'][^>]*>', '', html)
        html = re.sub(r'<link[^>]*rel=["\']search["\'][^>]*>', '', html)

        # 3. 移除 Atom 订阅链接
        html = re.sub(r'<link[^>]*rel=["\']alternate["\'][^>]*>', '', html)

        # 4. 移除 meta generator 中的本地信息
        html = re.sub(r'<meta name="generator"[^>]*>', '', html)

        # 5. 移除内联的 RLQ 脚本（动态加载会失败）
        # 保留 RLCONF 和 RLSTATE 数据，但移除会尝试加载模块的代码
        html = re.sub(
            r'<script>\(RLQ=window\.RLQ\|\|\[\]\)\.push\(function\(\)\{mw\.loader\.impl[^<]*</script>',
            '',
            html
        )

        # 6. 移除会尝试动态加载模块的脚本
        html = re.sub(
            r'<script>\(RLQ=window\.RLQ\|\|\[\]\)\.push\(function\(\)\{[^<]*?mw\.loader\.load[^<]*?</script>',
            '',
            html
        )

        # 7. 将 RLPAGEMODULES 设置为空数组（避免动态加载模块）
        html = re.sub(
            r'RLPAGEMODULES=\[[^\]]*\]',
            'RLPAGEMODULES=[]',
            html
        )

        return html

# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  MediaWiki 静态化工具")
    print("=" * 60)

    # 检查服务器是否运行
    try:
        http_get(f"{BASE_URL}{SCRIPT_PATH}/index.php", timeout=10)
        print(f"[✓] 检测到 MediaWiki 服务器: {BASE_URL}{SCRIPT_PATH}")
    except Exception as e:
        print(f"[✗] 无法连接 MediaWiki 服务器: {e}")
        print("    请先运行 start_wiki.bat 启动服务器")
        sys.exit(1)

    # 清理输出目录
    if OUTPUT_DIR.exists():
        print(f"[1/5] 清理输出目录: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 创建子目录
    (OUTPUT_DIR / "resources" / "css").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "resources" / "js").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "images").mkdir(parents=True, exist_ok=True)

    # 获取所有页面
    print("\n[2/5] 获取页面列表...")
    pages = get_all_pages()
    print(f"  共找到 {len(pages)} 个页面")

    # 过滤需要跳过的页面
    filtered_pages = []
    for page in pages:
        title = page["title"]
        if any(title.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        filtered_pages.append(page)

    print(f"  过滤后 {len(filtered_pages)} 个页面需要转换")

    # 构建页面映射：title -> 文件名
    page_map = {}
    for page in filtered_pages:
        title = page["title"]
        filename = safe_filename(title) + ".html"
        page_map[title] = filename

    # 首页映射
    page_map["首页"] = "index.html"

    # 初始化资源收集器
    collector = ResourceCollector(OUTPUT_DIR)

    # 抓取所有页面
    print("\n[3/5] 抓取页面内容...")
    page_contents = {}
    for i, page in enumerate(filtered_pages):
        title = page["title"]
        print(f"  [{i+1}/{len(filtered_pages)}] 抓取: {title}")
        html = get_page_html(title)
        if html:
            page_contents[title] = html
            collector.collect_from_html(html)

    # 确保首页被抓取
    if "首页" not in page_contents:
        print("  抓取首页...")
        homepage_html = get_page_html("首页")
        if homepage_html:
            page_contents["首页"] = homepage_html
            collector.collect_from_html(homepage_html)

    # 下载资源
    collector.download_all()

    # 重写 HTML 并保存
    print("\n[4/5] 重写 HTML 并保存...")
    rewriter = HTMLRewriter(collector, page_map, OUTPUT_DIR)

    for title, html in page_contents.items():
        filename = page_map.get(title, safe_filename(title) + ".html")
        print(f"  处理: {title} -> {filename}")

        # 重写 HTML
        rewritten = rewriter.rewrite(html, title)

        # 保存文件
        filepath = OUTPUT_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(rewritten)

    # 生成配置文件
    print("\n[5/5] 生成配置文件...")
    _generate_readme(OUTPUT_DIR)
    _generate_404(OUTPUT_DIR)

    # 统计
    print("\n" + "=" * 60)
    print("  静态化完成！")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  页面数量: {len(page_contents)}")
    print(f"  下载资源: {len(collector.downloaded)}")
    print("=" * 60)
    print("\n部署到 GitHub Pages:")
    print("  1. 将 site/ 目录的内容推送到 GitHub 仓库")
    print("  2. 在仓库 Settings -> Pages 中启用 GitHub Pages")
    print("  3. 选择 main 分支的 site/ 目录作为发布源")
    print("\n本地预览:")
    print("  python -m http.server 8000 --directory site")
    print("  然后访问 http://localhost:8000")

def _generate_readme(output_dir: Path):
    """生成 README.md"""
    readme = f"""# {SITE_NAME} - 静态站点

这是由 MediaWiki 转换生成的静态站点，可在 GitHub Pages 上托管。

## 部署方法

### 方法一：GitHub Pages（推荐）

1. 将 `site/` 目录的内容推送到 GitHub 仓库
2. 在仓库 **Settings → Pages** 中：
   - Source 选择 **Deploy from a branch**
   - Branch 选择 `main`，目录选择 `/site`
3. 保存后等待几分钟，即可通过 `https://<用户名>.github.io/<仓库名>/` 访问

### 方法二：本地预览

```bash
python -m http.server 8000 --directory site
```

然后访问 http://localhost:8000

## 说明

- 本静态站点由 `generate_static.py` 自动生成
- 页面中的动态元素（搜索、登录、编辑等）已保留但无作用
- 如需更新内容，请重新运行 `generate_static.py`
"""
    with open(output_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme)

def _generate_404(output_dir: Path):
    """生成 404 页面"""
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>404 - 页面不存在</title>
<style>
body { font-family: sans-serif; text-align: center; padding: 50px; }
h1 { color: #333; }
a { color: #36c; text-decoration: none; }
</style>
</head>
<body>
<h1>404</h1>
<p>抱歉，您访问的页面不存在。</p>
<p><a href="index.html">返回首页</a></p>
</body>
</html>
"""
    with open(output_dir / "404.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    main()
