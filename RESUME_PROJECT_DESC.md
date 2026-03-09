# 校招简历 · 项目描述参考

> 将本项目写进简历时，可根据岗位（后端 / 算法 / 全栈 / AI 应用）和篇幅选用下面某一版，并替换为你的 GitHub 链接与个人贡献点。

---

## 版本一：精简版（1～2 行，适合项目列表空间紧张）

**模块化 RAG 知识库服务（Python）**  
基于 MCP 协议对外提供检索与摘要能力；实现 PDF 摄取流水线（解析→分块→多模态增强→向量/BM25 双路编码）、混合检索（Dense + Sparse + RRF）与可选 Rerank，配套 Streamlit 管理端与全链路 Trace 可观测。技术栈：Chroma、LangChain Splitter、多 LLM/Embedding 可插拔。

---

## 版本二：标准版（3～5 条 bullet，推荐）

**模块化 RAG MCP 知识库服务** | Python · Chroma · Streamlit · MCP  
*（可补充：个人 GitHub 链接）*

- **数据与检索链路**：设计并实现 PDF→Markdown→Chunk 摄取流水线，支持多模态图片描述（Vision LLM）、Dense/Sparse 双路编码与 Chroma + BM25 存储；查询侧实现混合检索（向量 + BM25 + RRF 融合）与可选 Cross-Encoder/LLM Rerank，提升 Top-K 准确率。
- **协议与可插拔**：基于 Model Context Protocol (MCP) 暴露 `query_knowledge_hub`、`list_collections`、`get_document_summary` 等工具，可被 Copilot/Claude 等客户端直接调用；LLM、Embedding、Reranker、VectorStore 均抽象接口 + 工厂，通过配置切换（OpenAI/Azure/DeepSeek/Ollama 等），零代码改动的 A/B 与迁移。
- **可观测与运维**：全链路 Trace（Ingestion/Query 各阶段耗时与中间结果）落盘 JSONL；基于 Streamlit 搭建六页管理端：系统总览、数据浏览、Ingestion 管理、Query/Ingestion 追踪、评估面板，便于排查与迭代。
- **工程与质量**：统一类型与配置（YAML + Settings 校验）、单元/集成测试（pytest）、可选 Ragas + 自定义评估指标，支持 golden set 回归。

---

## 版本三：详细版（适合「项目经历」单独成段、或附在作品集说明里）

**项目名称**：Modular RAG MCP Server — 可插拔 RAG 知识库与 MCP 工具服务  

**项目描述**：  
面向校招/实习简历的 RAG 全栈项目：从文档摄取、多路编码与存储，到混合检索、重排序与 MCP 协议暴露，具备完整可观测与配置化能力。可作为「私有知识库 + AI 助手」的 backend 或学习 RAG/LLM 工程化的实战样本。

**技术亮点**：  
- **Ingestion**：PDF 解析（MarkItDown + PyMuPDF）、语义分块（LangChain RecursiveCharacterTextSplitter）、可插拔 Transform（Chunk 精炼、元数据增强、多模态 Image Captioning）、Dense/Sparse 双路编码与 Chroma + BM25 存储，支持 SHA256 增量跳过与幂等 Upsert。  
- **Retrieval**：Query 预处理 → Dense Retriever + Sparse Retriever 并行 → RRF 融合 → 可选 Rerank（Cross-Encoder / LLM），响应组装含引用与多模态信息。  
- **MCP**：基于官方 MCP SDK，stdio 传输，暴露 query_knowledge_hub / list_collections / get_document_summary，供 Copilot、Claude Desktop 等调用。  
- **可观测**：Trace 贯穿 Ingestion/Query；Streamlit Dashboard 六页（总览、数据浏览、Ingestion 管理、Query/Ingestion 追踪、评估）；支持 Ragas + 自定义评估与回归。  

**技术栈**：Python 3.10+、Chroma、LangChain-text-splitters、MCP SDK、Streamlit、YAML 配置、pytest；LLM/Embedding 支持 OpenAI、Azure、DeepSeek、Ollama 等可配置切换。

**可写角色**：独立开发 / 核心开发 / 与 XX 同学协作（若属实）。

---

## 撰写建议

1. **岗位匹配**：投算法/检索岗可突出 Hybrid Search、Rerank、评估；投后端可突出 Pipeline、MCP、可观测与配置化；投 AI 应用可突出 RAG 全链路与多模态。
2. **量化与结果**：若有数据可补充，例如「支持 XX 文档/Chunk 规模」「Rerank 后 MRR 提升 X%」「Trace 覆盖 Ingestion/Query 共 N 个阶段」。
3. **个人贡献**：明确写你负责的部分（如「独立实现 Ingestion Pipeline 与 MCP 三层工具」「负责 Hybrid Search 与 Rerank 模块设计与实现」），避免笼统。
4. **链接**：在简历或作品集中附 GitHub 仓库链接；若已部署 Demo，可附访问地址。
5. **状态说明**：若面试被问到，可说明「项目主体功能已完成，文档与部分边界 case 仍在完善」，并准备好讲清架构与关键代码路径（可参考 LEARNING_PATH.md）。

---

*按需选用其中一版，替换为你的信息与链接即可。*
