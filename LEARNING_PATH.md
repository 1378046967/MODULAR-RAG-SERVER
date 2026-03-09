# Modular RAG MCP Server — 代码学习路径

> 建议按阶段顺序学习，每阶段先读指定文件再跑相关测试，必要时配合 `DEV_SPEC.md` 对应章节。

---

## 阶段 0：先建立全局图景（约 30 分钟）

**目标**：知道项目做什么、两条主链路是什么、数据怎么流动。

1. **读文档**
   - [README.md](README.md) — 项目定位、分支、概览
   - [DEV_SPEC.md](DEV_SPEC.md) 第 1–2 节（项目概述、核心特点）

2. **看配置与入口**
   - [config/settings.yaml](config/settings.yaml) — 各模块的开关与参数（LLM、Embedding、检索、Rerank、观测等）
   - [main.py](main.py) — 当前入口（只加载配置）
   - [src/mcp_server/server.py](src/mcp_server/server.py) — 实际 MCP 启动（stdio + 日志重定向）

3. **心里要能回答**
   - Ingestion 链路：从文件到向量/BM25/图片存储，经过哪几步？
   - Query 链路：一次 `query_knowledge_hub` 调用会经过哪些组件？
   - MCP 暴露了哪三个 Tool？

---

## 阶段 1：核心类型与配置（约 1 小时）

**目标**：所有管道阶段用的数据结构都在这里，后面读代码会反复遇到。

### 1.1 数据类型 — 必读

| 顺序 | 文件 | 重点 |
|------|------|------|
| 1 | [src/core/types.py](src/core/types.py) | `Document`（Loader 输出）、`Chunk`（Splitter/Transform 输出）、`ChunkRecord`（带向量，进存储）、`ProcessedQuery`、`RetrievalResult`；`to_dict`/`from_dict` 序列化约定 |

关注：`Document.metadata` 必含 `source_path`；Chunk 的 `[IMAGE: id]` 占位与 `metadata.images`；`ChunkRecord.from_chunk()` 如何从 Chunk 生成待存记录。

### 1.2 配置加载

| 顺序 | 文件 | 重点 |
|------|------|------|
| 2 | [src/core/settings.py](src/core/settings.py) | `load_settings()`、`resolve_path()`；各配置块如何被解析成结构化对象（LLM、Embedding、VectorStore、Retrieval、Rerank、Observability、Ingestion） |

可选：跑单测巩固类型与配置：

```bash
pytest tests/unit/test_core_types.py tests/unit/test_config_loading.py -v
```

---

## 阶段 2：Libs 层 — 可插拔基础能力（约 2–3 小时）

**目标**：理解“接口 + 工厂”的插拔方式，知道每种能力有哪些实现。

### 2.1 抽象接口（先看 base，再看一个实现）

建议按「接口 → 一个实现 → 工厂」的顺序读，每类选一个实现即可。

| 主题 | 阅读顺序 | 文件 | 重点 |
|------|----------|------|------|
| Loader | 1→2→3 | [src/libs/loader/base_loader.py](src/libs/loader/base_loader.py) | `BaseLoader` 契约 |
| | | [src/libs/loader/pdf_loader.py](src/libs/loader/pdf_loader.py) | PDF → Markdown `Document`，metadata（source_path, page, images） |
| | | [src/libs/loader/file_integrity.py](src/libs/loader/file_integrity.py) | SHA256 + SQLite 增量跳过 |
| Splitter | 1→2→3 | [src/libs/splitter/base_splitter.py](src/libs/splitter/base_splitter.py) | `BaseSplitter` 契约 |
| | | [src/libs/splitter/recursive_splitter.py](src/libs/splitter/recursive_splitter.py) | 基于 LangChain 的递归切分 |
| | | [src/libs/splitter/splitter_factory.py](src/libs/splitter/splitter_factory.py) | 根据配置创建 Splitter |
| Embedding | 1→2→3 | [src/libs/embedding/base_embedding.py](src/libs/embedding/base_embedding.py) | `BaseEmbedding` 契约 |
| | | [src/libs/embedding/azure_embedding.py](src/libs/embedding/azure_embedding.py) 或 [openai_embedding.py](src/libs/embedding/openai_embedding.py) | 调用外部 API 得到向量 |
| | | [src/libs/embedding/embedding_factory.py](src/libs/embedding/embedding_factory.py) | 根据配置创建 Embedding |
| VectorStore | 1→2→3 | [src/libs/vector_store/base_vector_store.py](src/libs/vector_store/base_vector_store.py) | 抽象接口（query、upsert、delete 等） |
| | | [src/libs/vector_store/chroma_store.py](src/libs/vector_store/chroma_store.py) | Chroma 实现 |
| | | [src/libs/vector_store/vector_store_factory.py](src/libs/vector_store/vector_store_factory.py) | 工厂 |
| LLM | 1→2 | [src/libs/llm/base_llm.py](src/libs/llm/base_llm.py) | `BaseLLM` 契约 |
| | | [src/libs/llm/llm_factory.py](src/libs/llm/llm_factory.py) | 多 provider 创建逻辑 |
| | | 任选其一 | [azure_llm.py](src/libs/llm/azure_llm.py) / [ollama_llm.py](src/libs/llm/ollama_llm.py) |
| Reranker | 1→2→3 | [src/libs/reranker/base_reranker.py](src/libs/reranker/base_reranker.py) | `BaseReranker` 契约 |
| | | [src/libs/reranker/cross_encoder_reranker.py](src/libs/reranker/cross_encoder_reranker.py) 或 [llm_reranker.py](src/libs/reranker/llm_reranker.py) | 一种实现 |
| | | [src/libs/reranker/reranker_factory.py](src/libs/reranker/reranker_factory.py) | 工厂 |

可选：跑 Libs 相关单测（loader、splitter、embedding、vector_store、llm、reranker）。

---

## 阶段 3：Ingestion 管道（约 2 小时）

**目标**：从「一个文件路径」到「向量 + BM25 + 图片」的完整流程，以及各阶段如何用 Trace 打点。

### 3.1 管道编排 — 核心

| 顺序 | 文件 | 重点 |
|------|------|------|
| 1 | [src/ingestion/pipeline.py](src/ingestion/pipeline.py) | `IngestionPipeline`：FileIntegrity → Loader → Chunker → Transform → DenseEncoder + SparseEncoder → VectorUpserter + BM25Indexer + ImageStorage；`PipelineResult`；进度回调与错误处理 |

建议：在 IDE 里从 `run()` 或 `run_single_file()` 顺着调用链走一遍，看每个 stage 的输入输出类型（Document → Chunk → ChunkRecord）。

### 3.2 管道各阶段实现（按需深入）

| 阶段 | 文件 | 重点 |
|------|------|------|
| Chunking | [src/ingestion/chunking/document_chunker.py](src/ingestion/chunking/document_chunker.py) | 如何把 `Document` 变成 `Chunk` 列表（调用 Libs Splitter、生成 id、metadata） |
| Transform | [src/ingestion/transform/base_transform.py](src/ingestion/transform/base_transform.py) | Transform 接口 |
| | [src/ingestion/transform/chunk_refiner.py](src/ingestion/transform/chunk_refiner.py) | LLM 改写/润色 Chunk |
| | [src/ingestion/transform/metadata_enricher.py](src/ingestion/transform/metadata_enricher.py) | 元数据增强（标题、摘要等） |
| | [src/ingestion/transform/image_captioner.py](src/ingestion/transform/image_captioner.py) | 多模态：图 → Caption，写入 chunk 或 metadata |
| Encode | [src/ingestion/embedding/dense_encoder.py](src/ingestion/embedding/dense_encoder.py) | Chunk → 调用 Embedding → 向量 |
| | [src/ingestion/embedding/sparse_encoder.py](src/ingestion/embedding/sparse_encoder.py) | Chunk → BM25 稀疏向量 |
| | [src/ingestion/embedding/batch_processor.py](src/ingestion/embedding/batch_processor.py) | 批处理与并发控制 |
| Storage | [src/ingestion/storage/vector_upserter.py](src/ingestion/storage/vector_upserter.py) | ChunkRecord → Chroma upsert，幂等 |
| | [src/ingestion/storage/bm25_indexer.py](src/ingestion/storage/bm25_indexer.py) | BM25 索引的更新与查询 |
| | [src/ingestion/storage/image_storage.py](src/ingestion/storage/image_storage.py) | 图片文件存储与路径约定 |
| 文档管理 | [src/ingestion/document_manager.py](src/ingestion/document_manager.py) | 对「已摄入文档」的查询/删除等（与 Pipeline 的配合） |

可选：跑 Ingestion 相关测试：

```bash
pytest tests/unit/test_document_chunker.py tests/unit/test_chunk_refiner.py tests/unit/test_dense_encoder.py tests/unit/test_vector_upserter_idempotency.py -v
pytest tests/integration/test_ingestion_pipeline.py -v  # 需要环境/配置
```

---

## 阶段 4：Query 链路 — 检索与响应（约 2 小时）

**目标**：一次查询如何变成「Dense + Sparse → RRF → 可选 Rerank → 带引用的回答」。

### 4.1 检索核心

| 顺序 | 文件 | 重点 |
|------|------|------|
| 1 | [src/core/query_engine/query_processor.py](src/core/query_engine/query_processor.py) | 查询预处理 → `ProcessedQuery`（关键词、filter 等） |
| 2 | [src/core/query_engine/dense_retriever.py](src/core/query_engine/dense_retriever.py) | 向量检索接口与实现 |
| 3 | [src/core/query_engine/sparse_retriever.py](src/core/query_engine/sparse_retriever.py) | BM25 检索 |
| 4 | [src/core/query_engine/fusion.py](src/core/query_engine/fusion.py) | RRF 融合算法 |
| 5 | [src/core/query_engine/hybrid_search.py](src/core/query_engine/hybrid_search.py) | 编排：QueryProcessor → Dense + Sparse（可并行）→ RRFFusion → 可选 metadata 过滤；`HybridSearchConfig` |
| 6 | [src/core/query_engine/reranker.py](src/core/query_engine/reranker.py) | 对 HybridSearch 结果做 Rerank（调用 Libs Reranker） |

### 4.2 响应与引用

| 顺序 | 文件 | 重点 |
|------|------|------|
| 7 | [src/core/response/response_builder.py](src/core/response/response_builder.py) | 检索结果 → 结构化回答（含引用、source_path 等） |
| 8 | [src/core/response/citation_generator.py](src/core/response/citation_generator.py) | 引用格式生成 |
| 9 | [src/core/response/multimodal_assembler.py](src/core/response/multimodal_assembler.py) | 若 Chunk 含 `[IMAGE: id]`，如何把图片信息拼进响应 |

可选：跑 Query 相关单测：

```bash
pytest tests/unit/test_query_processor.py tests/unit/test_dense_retriever.py tests/unit/test_sparse_retriever.py tests/unit/test_fusion_rrf.py tests/unit/test_response_builder.py -v
```

---

## 阶段 5：MCP 层 — 协议与三个 Tool（约 1.5 小时）

**目标**：MCP 如何启动、如何注册与调用 Tool、三个 Tool 各自做什么。

### 5.1 协议与启动

| 顺序 | 文件 | 重点 |
|------|------|------|
| 1 | [src/mcp_server/protocol_handler.py](src/mcp_server/protocol_handler.py) | `ProtocolHandler`：`register_tool`、`get_tool_schemas`、执行 handler 时的错误码与包装 |
| 2 | [src/mcp_server/server.py](src/mcp_server/server.py) | stdio 传输、日志重定向、`create_mcp_server()` 与 `server.run()` |

### 5.2 三个 Tool 的实现

| 顺序 | 文件 | 重点 |
|------|------|------|
| 3 | [src/mcp_server/tools/query_knowledge_hub.py](src/mcp_server/tools/query_knowledge_hub.py) | 入参（query, top_k, collection）→ 加载配置 → HybridSearch → 可选 Reranker → ResponseBuilder → MCP 返回格式；Trace 记录 |
| 4 | [src/mcp_server/tools/list_collections.py](src/mcp_server/tools/list_collections.py) | 列出 VectorStore 的 collections |
| 5 | [src/mcp_server/tools/get_document_summary.py](src/mcp_server/tools/get_document_summary.py) | 按文档 id 或 source 返回摘要信息 |

建议：在 `protocol_handler.py` 里找到「工具注册」的代码，看三个 Tool 的 name、schema、handler 是如何挂上去的。

可选：跑 MCP 相关测试：

```bash
pytest tests/unit/test_protocol_handler.py tests/unit/test_query_knowledge_hub.py tests/unit/test_list_collections.py tests/unit/test_get_document_summary.py -v
```

---

## 阶段 6：可观测 — Trace 与 Dashboard（约 1 小时）

**目标**：Trace 如何产生、存储，Dashboard 如何消费这些数据。

### 6.1 Trace

| 顺序 | 文件 | 重点 |
|------|------|------|
| 1 | [src/core/trace/trace_context.py](src/core/trace/trace_context.py) | `TraceContext`：当前请求/管道的 trace 上下文 |
| 2 | [src/core/trace/trace_collector.py](src/core/trace/trace_collector.py) | 收集、写入（如 JSONL）、查询 trace |

可结合：Ingestion Pipeline 和 Query 链路里哪里 `push_span` / 记录阶段结果。

### 6.2 Dashboard（Streamlit）

| 顺序 | 文件 | 重点 |
|------|------|------|
| 3 | [src/observability/dashboard/app.py](src/observability/dashboard/app.py) | 多页入口与路由 |
| 4 | [src/observability/dashboard/services/trace_service.py](src/observability/dashboard/services/trace_service.py) | 读取 trace 数据供页面使用 |
| 5 | 任选 1–2 页 | [overview.py](src/observability/dashboard/pages/overview.py)、[data_browser.py](src/observability/dashboard/pages/data_browser.py)、[query_traces.py](src/observability/dashboard/pages/query_traces.py)、[ingestion_manager.py](src/observability/dashboard/pages/ingestion_manager.py) |

---

## 阶段 7：评估体系（可选，约 1 小时）

**目标**：评估如何插拔、Ragas 与自定义指标如何被调用。

| 顺序 | 文件 | 重点 |
|------|------|------|
| 1 | [src/libs/evaluator/base_evaluator.py](src/libs/evaluator/base_evaluator.py) | 评估接口 |
| 2 | [src/observability/evaluation/composite_evaluator.py](src/observability/evaluation/composite_evaluator.py) | 组合多个 evaluator |
| 3 | [src/observability/evaluation/ragas_evaluator.py](src/observability/evaluation/ragas_evaluator.py) | Ragas 集成 |
| 4 | [src/observability/evaluation/eval_runner.py](src/observability/evaluation/eval_runner.py) | 运行评估任务、产出指标 |

---

## 学习顺序小结（按依赖关系）

```
阶段 0 全局图景
    ↓
阶段 1 核心类型与配置 (types, settings)
    ↓
阶段 2 Libs 层 (loader, splitter, embedding, vector_store, llm, reranker)
    ↓
阶段 3 Ingestion 管道 (pipeline → chunking/transform/encode/storage)
    ↓
阶段 4 Query 链路 (query_processor → dense/sparse → fusion → reranker → response_builder)
    ↓
阶段 5 MCP (protocol_handler, server, 三个 tools)
    ↓
阶段 6 可观测 (trace, dashboard)
    ↓
阶段 7 评估 (可选)
```

---

## 实践建议

1. **每阶段后跑对应单测**：巩固「输入输出」和「边界行为」。
2. **改配置再跑**：例如在 `config/settings.yaml` 里切换 `llm.provider`、`rerank.enabled`，看代码里哪里读这些配置。
3. **打断点跟一条请求**：从 `query_knowledge_hub` 的 handler 一路跟到 `HybridSearch.search()` 和 `ResponseBuilder.build()`。
4. **对照 DEV_SPEC**：读「系统架构与模块设计」「技术选型」等章节，和代码一一对应。

按上述路径走完，你就能在「类型 → 配置 → Libs → Ingestion → Query → MCP → 观测」这条线上把整个项目串起来。
