# 典型工作流

## 1. 打开网页并抓取内容

```bash
python browser_launch.py --new "https://example.com"        # 拿到新 tab 的 id
python browser_nav.py --tab <id> --goto "https://example.com"
python browser_extract.py --tab <id> --mode text             # 纯文本正文，适合直接喂给模型分析
python browser_extract.py --tab <id> --mode links            # 所有链接
python browser_extract.py --tab <id> --mode meta             # 标题/描述/h1
```

大页面注意 `--max-chars`（默认 20000）截断，需要完整内容时用 `--save out.txt` 写文件后自己分段读。

## 2. "看图操作"式的表单填写/点击（computer-use 风格）

```bash
python browser_screenshot.py --tab <id> --out shot.png --annotate
# 产出 shot.png（带编号红框）+ shot.elements.json（编号 -> 元素信息，tag/text/rect等）
# 把 shot.png 发给用户看/自己视觉分析，确定要操作第几号元素
python browser_input.py --tab <id> --type-index 5 --text "张三" --clear-first
python browser_input.py --tab <id> --click-index 8
python browser_screenshot.py --tab <id> --out after.png --annotate   # 操作后再截一次确认结果
```

也可以不截图，直接用 CSS 选择器：
```bash
python browser_input.py --tab <id> --click-selector "#submit-btn"
python browser_input.py --tab <id> --type-selector "input[name=username]" --text "abc"
```

## 3. 与用户协作（不同介入程度）

- **观察模式**（只看不动）：`browser_extract.py --mode text` / `browser_screenshot.py` 直接读取
  用户当前 tab（用 `--url-contains`/`--title-contains` 定位到用户正在看的那个 tab，不要用 `--new`
  开新 tab，否则不是用户正在看的页面）。
- **建议模式**：观察后只用文字描述"你可以点击左上角的登录按钮"，不调用 `browser_input.py`。
- **代劳模式**：用户明确同意后才调用 `browser_input.py` 实际操作；操作前后各截一次图，把结果给用户确认，
  不要连续做多步高风险操作（比如"提交订单""转账确认"）而不中途反馈。
- **等待用户完成某步**（比如让用户自己输入验证码/完成支付，Agent 等结果）：
  ```bash
  python browser_watch.py --tab <id> --wait-url-contains "/success" --timeout 120 --interval 2
  ```

## 4. 调试网页问题

```bash
python browser_console.py --tab <id> --eval "document.querySelectorAll('.item').length"
python browser_console.py --tab <id> --watch-console --duration 5   # 抓最近5秒的console报错
python browser_console.py --tab <id> --watch-network --duration 5   # 抓最近5秒的请求（url/status）
```
