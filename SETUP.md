# 环境安装说明

## 已完成的安装

本项目已在仓库内创建虚拟环境并安装依赖：

- **虚拟环境路径**：`.venv`（项目根目录下）
- **主依赖**：来自 `pyproject.toml`（pyyaml、langchain-text-splitters、chromadb、mcp 等）
- **开发依赖**：`pip install -e ".[dev]"`（pytest、ruff、mypy、openai 等）
- **额外运行时依赖**：streamlit（Dashboard）、markitdown、pymupdf、Pillow（PDF 解析与图片处理）

## 使用虚拟环境

在项目根目录下，用虚拟环境里的 Python/pip 运行命令：

```bash
# 激活虚拟环境（任选一种方式）
source .venv/bin/activate   # Linux/macOS
# 或直接使用 .venv 下的可执行文件，无需 activate：
.venv/bin/python ...
.venv/bin/pip ...
```

### 常用命令示例

```bash
# 启动 Dashboard
.venv/bin/python scripts/start_dashboard.py

# 运行摄取脚本
.venv/bin/python scripts/ingest.py --path <文件或目录> --collection <集合名>

# 运行测试
.venv/bin/pytest tests/unit -v

# 加载配置并检查
.venv/bin/python -c "from src.core.settings import load_settings; load_settings('config/settings.yaml'); print('OK')"
```

## 若需在新机器上重新安装

1. 进入项目根目录，创建虚拟环境并安装项目与 dev 依赖：

   ```bash
   cd /path/to/MODULAR-RAG-MCP-SERVER
   python3 -m venv .venv
   .venv/bin/python -m ensurepip --upgrade
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -e ".[dev]"
   ```

2. 安装 Dashboard 与 PDF 相关依赖（代码中用到了但未写在 pyproject.toml）：

   ```bash
   .venv/bin/pip install streamlit markitdown pymupdf Pillow
   ```

3. 可选：需要 Cross-Encoder Rerank 时安装 `sentence-transformers`；需要 Ragas 评估时安装 `ragas datasets`：

   ```bash
   .venv/bin/pip install sentence-transformers   # Rerank
   .venv/bin/pip install ragas datasets          # 评估
   ```

## 配置

编辑 `config/settings.yaml` 填写 LLM、Embedding、Azure 等 API 端点与密钥；部分项可通过环境变量覆盖（如 `AZURE_OPENAI_API_KEY`）。
