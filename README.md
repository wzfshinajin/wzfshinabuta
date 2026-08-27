# 伍寇维基 - 静态站点

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
