# 拆解这个打包+API变更两件分开的事情
这里其实是**两个独立问题叠加在一起**，不是单纯一个打包bug：

## 1）griffelib 2.0.0：真正的打包缺陷
在 `2.0.0` 的发布包里面，作者构建wheel的时候配置出错：
- 打包脚本只把 `griffelib.dist-info`（包元信息，就是`uv pip list`能读到版本的那部分）打进压缩包；
- **漏掉了 `griffe/` 源码文件夹**。

现象就是：
`uv pip list` 能看到 `griffelib==2.0.0`，看起来安装成功，但是site-packages下面没有`griffe`源码目录，`import griffe`直接`ModuleNotFoundError`。
> 这个是**构建脚本/setuptools的打包失误**。作者在2.0.1、2.0.2修复了打包配置，wheel里面带上`griffe/`源码，所以升级到2.0.2之后`import griffe`不再报找不到模块。

## 2）2.x 大版本重构：删掉顶层 `__version__`（不是打包bug，是代码设计变更）
升级到2.0.2之后，`import griffe`成功了，但是`griffe.__version__`报错。
这**不是打包问题，是API破坏性改动**：
- 在 `griffe 1.x`：顶层`__init__.py`直接暴露`__version__`，写`griffe.__version__`就能拿到版本号；
- 在 `2.0+`：作者清理顶层命名空间，不再在`griffe`根模块放`__version__`。版本字符串放到内部私有模块`griffe._internal`，不鼓励外部直接访问私有模块。

✅ 推荐标准做法：用Python内置库`importlib.metadata`从包元数据读取版本，**不碰库内部代码**：
```python
import importlib.metadata
print(importlib.metadata.version("griffelib"))
```
好处：不管库内部怎么重构版本变量，只要PyPI包名叫griffelib，这个方法永远能用。

# 理清命名关系（这个项目命名特别绕，很多人踩坑）
1. PyPI包名：`griffelib` → 安装用，`uv add griffelib`
2. Python导入模块名：`griffe` → `import griffe`
3. 还有一个包叫`griffe`（元包）：`griffe = griffelib + griffecli`，附带CLI命令行工具
> 作者把项目本体名字叫 **Griffe**。拆分后：
> - griffelib：纯库（无CLI，适合代码静态分析嵌入你的程序）
> - griffecli：仅命令行程序
> - griffe：总元包，一次性装上面两个

# 顺带小提醒
这种坑在Python生态还挺常见：
- 包名 ≠ import模块名（例子很多：`python-dateutil`导入`dateutil`，`pyyaml`导入`yaml`）
- 大版本升级，会删除旧的顶层属性（`__version__`是高频牺牲品）
- 偶尔发布的某个版本wheel打包漏文件，就像2.0.0这种“幽灵包”，pip/uv能识别包存在，但没有源码。

## 测试一下完整功能
你可以直接运行这个，确认解析功能正常：
```powershell
uv run python -c "import griffe; mod = griffe.load('math');print('✅成功加载math模块');print('math.sqrt:', mod.functions['sqrt'].signature)"
```
运行后能打印sqrt的函数签名，说明整个库完全正常可用。

如果你感兴趣，我们可以拿你项目里一个`.py`文件，用griffe读取里面自定义函数、类、docstring，体验它静态解析的能力。