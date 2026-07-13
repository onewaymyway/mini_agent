# Python 命令检测（极易踩坑，务必遵守）

**本环境同时存在 `python` 和 `python3` 两个命令，但只有一个是真正可用的。**

## 第一步：检测哪个 Python 可用

在调用任何浏览器 CDP 脚本之前，**必须先检测哪个命令可用**，然后后续所有调用都使用那个可用的命令。

```bash
# 先测试 python 是否可用
python --version 2>&1 | head -1
# 如果输出类似 "Python 3.x.x"，则用 python
# 如果报错 "不是内部或外部命令"，则用 python3

# 再测试 python3 是否可用（作为备选）
python3 --version 2>&1 | head -1
```

## 本环境的检测结果

- **`python`** ✅ 可用（指向 Anaconda 的 Python，路径如 `D:\ProgramData\anaconda3\python.exe`）
- **`python3`** ❌ 不可用（指向 Windows 应用商店的重定向器，会弹出安装提示）

**因此，本环境中所有浏览器 CDP 脚本都必须使用 `python` 而不是 `python3` 来调用！**

## 正确用法示例

```bash
# ✅ 正确：使用 python（Anaconda 版本）
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"
python browser_extract.py --tab <id> --mode text --save ./temp_data/page_content.txt

# ❌ 错误：使用 python3（会失败）
python3 browser_launch.py --dedicated --name work  # 报错！
```

## 为什么必须检测？

不同环境的 Python 命令可用性不同：
- **Windows + Anaconda**：通常只有 `python` 可用，`python3` 不存在或重定向
- **Linux/macOS**：通常 `python3` 可用，`python` 可能不存在（Python 2 已移除）
- **某些 Docker 容器**：可能两者都有或都没有

**每次在新环境中使用时，必须先运行检测命令确认，然后统一使用那个可用的命令。**

## 检测脚本（可选）

如果不确定当前环境，可以运行以下命令自动检测：

```bash
if command -v python &> /dev/null && python --version &> /dev/null; then
    echo "USE: python"
elif command -v python3 &> /dev/null && python3 --version &> /dev/null; then
    echo "USE: python3"
else
    echo "ERROR: No Python found!"
fi
```
