# MCPServer

基于 MCP（Model Context Protocol）的自定义服务框架，内置搜索、向量数据库、数学计算、终端控制等模块，可自由扩展开发。

## 功能

- MCP 基础服务框架：工具注册、资源暴露、参数校验
- 搜索引擎（Search）
- 向量数据库：基于 ChromaDB 的存储与相似度检索（默认 CPU 推理，可切换 GPU）
- 数学计算工具（Calc）
- 终端命令执行工具（Terminal）
- 灵活扩展：只需添加新模块并修改配置文件

## 环境要求

- Python >= 3.10
- 推荐 [uv](https://docs.astral.sh/uv/) 或使用 pip

## 安装与部署

### 1. 克隆仓库
```bash
git clone https://github.com/riverwithyou/MCPServer.git
cd MCPServer
```

2. 创建虚拟环境（以 conda 为例）

```bash
conda create -n MCPServer python=3.10 -y
conda activate MCPServer
```

3. 安装 PyTorch（CPU 版）

```bash
# 使用 uv（推荐）
uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# 或使用 pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

4. 安装项目依赖

```bash
# 使用 uv
uv pip install -r requirements.txt

# 或使用 pip
pip install -r requirements.txt
```

5. 安装 Playwright 浏览器驱动（搜索模块需要）

```bash
playwright install
# Linux 用户如需系统依赖，可额外运行：playwright install-deps
```

6. （可选）下载向量模型

向量数据库默认使用 Qwen3-Embedding-0.6B，需要自行下载并放置于 Models/ 目录（或修改配置中的模型路径）。
下载源：

· Hugging Face
· ModelScope

## 启动服务

```bash
python main.py
```

## 配置说明

主配置文件位于 Config/tools.yaml，可控制每个工具的启用/停用及其他参数。

向量数据库默认禁用，如需使用请在配置中设置 enable: true。

## 开发扩展

新增 MCP 工具的步骤：

1. 在 Tools/ 下新建一个模块目录，例如 MyTool/。
2. 目录内必须包含三个文件：
   · MyTool.py （主程序，文件名与目录名相同）
   · register.py （注册入口，供框架调用）
   · Usage.yaml （定义工具的参数和说明）
3. 在 Config/tools.yaml 中注册该模块（添加路径和启用状态）。
4. 重启服务。

## 许可证

MIT License。详见 LICENSE 文件。

Copyright (c) 2026 riverwithyou
