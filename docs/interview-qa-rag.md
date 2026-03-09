# 校招面试：RAG / 本项目 常见问题与参考答案

本文档根据对本项目（Modular RAG MCP Server）的讨论整理，适合校招中「RAG 系统 / 检索增强 / 项目经历」类问题的准备。答案尽量简洁、可口头复述。

---

## 一、整体架构与流程

### Q1：你们 RAG 系统大致分哪几块？各自做什么？

**答**：分三块——**Ingestion（摄取）**、**Query（查询）**、**Evaluation（评估）**。

- **Ingestion**：文件 → 完整性检查 → 加载为 Document → 分块 Chunk → 变换（精炼、元数据增强、图片描述）→ 编码（Dense + Sparse）→ 写入向量库、BM25 索引、图片索引。
- **Query**：用户 query → 预处理（QueryProcessor）→ 双路检索（Dense + Sparse，可并行）→ RRF 融合 → 可选 metadata 后过滤 → 可选 Rerank → 返回 RetrievalResult。
- **Evaluation**：用 Golden 测试集，对每条用例做检索 → 生成答案 → 用 Evaluator（Custom/Ragas/Composite）打分，汇总得到 aggregate_metrics。

---

### Q2：MCP 暴露了哪几个 Tool？分别干什么？

**答**：三个。

1. **query_knowledge_hub**：用混合检索（Dense + Sparse + RRF）查知识库，支持 top_k、collection，可选 Rerank，返回带来源引用的结果。
2. **list_collections**：列出当前知识库下所有 collection 及基本信息（如文档数）。
3. **get_document_summary**：根据 doc_id 取单篇文档的摘要和元数据（标题、来源等）。

---

### Q2a：项目技术栈 / 存储大致是什么？

**答**：**存储**：向量用 **Chroma**（可配置），BM25 自建索引（chunk_id + 词频等），图片单独 **ImageStorage**。**解析与分块**：PDF 等经 Loader 转文本，分块用 **LangChain RecursiveCharacterTextSplitter**。**模型与接口**：LLM/Embedding/Vision/Reranker 都是抽象 + 工厂，通过 **config/settings.yaml** 选 provider（如 OpenAI、Azure、DeepSeek、Ollama），便于零代码切换。**协议**：对外通过 **MCP（Model Context Protocol）** 暴露 Tool，可被 Copilot、Claude Desktop 等调用。

---

## 二、Ingestion 阶段

### Q3：Ingestion 有哪几个阶段？每阶段输入输出是什么？

**答**：六个阶段。

| 阶段 | 输入 | 输出 |
|------|------|------|
| 1 完整性 | file_path | file_hash；已处理过则跳过返回 |
| 2 加载 | file_path | Document(id, text, metadata) |
| 3 分块 | Document | List[Chunk] |
| 4 变换 | List[Chunk] | 同一 List[Chunk]，增强 text/metadata |
| 5 编码 | List[Chunk] | BatchResult(dense_vectors, sparse_stats) |
| 6 存储 | chunks + vectors + sparse_stats + images | vector_ids、BM25 索引、图片索引 |

---

### Q4：Chunk ID 是怎么定义的？为什么有两处生成？

**答**：有两处生成，**最终用于存储和检索的是 VectorUpserter 生成的那个**。

- **DocumentChunker**（分块时）：格式 `{doc_id}_{index:04d}_{content_hash}`，用于 pipeline 里 Chunk 的 id，和文档内序号、内容 hash 相关。
- **VectorUpserter**（写入向量库时）：格式 `{source_hash}_{chunk_index:04d}_{content_hash}`，用 metadata 里的 source_path、chunk_index 和 chunk.text 的 hash。写入向量库和 BM25 的、以及检索/评估里用的 chunk_id 都是这一套。

两处都保证**确定性**：同一输入得到同一 ID，便于去重和可观测。

---

### Q5：Stage 4 变换管道具体做了哪三件事？

**答**：依次三件事，都是对同一批 Chunk 原地改 text 和 metadata。

1. **ChunkRefiner**：规则 + 可选 LLM 精炼正文（去噪、合并等），metadata 里记 `refined_by`（"rule" 或 "llm"）。
2. **MetadataEnricher**：规则 + 可选 LLM 抽取 title、summary、tags 等，写进 metadata，记 `enriched_by`。
3. **ImageCaptioner**：对 chunk 里 `[IMAGE: id]` 引用的图片用 Vision LLM 生成描述，写进 `metadata.image_captions`，可选把描述拼进 text。

---

### Q5a：Ingestion 各阶段用的具体组件叫什么？

**答**：按阶段对应关系记即可。

| 阶段 | 主要组件 |
|------|----------|
| 1 完整性 | **SQLiteIntegrityChecker**（file_hash，已处理则跳过） |
| 2 加载 | **Loader**（如 PdfLoader、MarkitdownLoader）→ Document |
| 3 分块 | **DocumentChunker** + **RecursiveCharacterTextSplitter**（LangChain）→ List[Chunk] |
| 4 变换 | **ChunkRefiner** → **MetadataEnricher** → **ImageCaptioner** |
| 5 编码 | **BatchProcessor** 调度 **DenseEncoder** + **SparseEncoder** → BatchResult |
| 6 存储 | **VectorUpserter**（写向量库）、**BM25Indexer**（建 BM25 索引）、**ImageStorage**（图片索引） |

向量库默认是 **Chroma**（可配置）；Dense 用 Embedding 接口，Sparse 用 BM25 统计（chunk_id、term_frequencies、doc_length）。

---

## 三、Query 阶段

### Q6：HybridSearch.search() 的几个参数分别是什么作用？

**答**：

- **query**：用户检索问句，会经 QueryProcessor 得到 keywords 和解析出的 filters；Dense 用原句做向量检索，Sparse 用 keywords 做 BM25。
- **top_k**：最终返回条数上限；为 None 时用 config.fusion_top_k。
- **filters**：调用方显式传入的 metadata 过滤条件（如 collection、doc_type），会和 query 里解析出的 filters 合并，用于检索和后过滤。
- **trace**：可观测上下文，各阶段会 record_stage 记耗时等。
- **return_details**：为 True 时返回 HybridSearchResult（含 dense_results、sparse_results、processed_query 等），为 False 时只返回 List[RetrievalResult]。

---

### Q7：filters 从哪里来？怎么用？

**答**：两个来源。

1. **Query 里解析**：QueryProcessor 用正则匹配 `key:value`（如 `collection:docs`），解析出 filters，并从 query 里去掉这段再分词。
2. **调用方传入**：search(..., filters={...})。  
两者会合并，**显式传入的覆盖同名字段**。合并后的 filters 用于：Dense 检索时传给向量库（若支持）；融合后再用 `_apply_metadata_filters` 在内存里按 metadata 筛一遍结果（兜底）。

---

### Q8：双路检索（Dense + Sparse）分别做什么？怎么融合？

**答**：

- **Dense**：用 query 的 embedding 在向量库里做相似度检索，返回语义相近的 chunk。
- **Sparse**：用 QueryProcessor 产出的 keywords 在 BM25 索引里检索，返回词匹配的 chunk；BM25 只存 id 和统计，正文从向量库 get_by_ids 拉取。

两路可并行执行，任一失败会降级用另一路。融合用 **RRF（Reciprocal Rank Fusion）**：对每个 chunk 算 RRF_score = Σ 1/(k+rank)，k 默认 60，再按 RRF 分排序取 top_k。

---

### Q9：_apply_metadata_filters 是干什么的？

**答**：在**融合之后、截断 top_k 之前**，用合并后的 filters 在内存里再筛一遍结果。因为 Dense 存储不一定完整支持 filter、Sparse 通常没按 metadata 过滤，所以用这一步做兜底，只保留 metadata 满足 collection、doc_type、tags、source_path 等条件的 chunk。

---

### Q10：Rerank 的流程是什么？数据在各阶段有什么不同？

**答**：Rerank 在 HybridSearch 之外，由调用方在拿到检索结果后调用。

1. **前置判断**：结果为空或仅 1 条直接返回；未启用或 NoneReranker 则按原顺序取 top_k。
2. **格式转换**：RetrievalResult → candidates（dict：id, text, score, metadata）。
3. **调用后端**：LLM 或 Cross-Encoder 对 (query, candidates) 打分排序，返回带 `rerank_score` 的 candidates。
4. **格式还原**：candidates → RetrievalResult，**score 改为 rerank_score**，metadata 里增加 **original_score**、**rerank_score**、**reranked=True**。
5. **截断**：取前 top_k，返回 RerankResult。

所以：**检索阶段**的 RetrievalResult 的 score 是检索/融合分；**rerank 输出**的 RetrievalResult 的 score 是 rerank 分，检索分存在 metadata.original_score。

---

### Q10a：检索结果如何变成带引用的 MCP 响应？

**答**：在 MCP 工具里（如 query_knowledge_hub）拿到 List[RetrievalResult] 后，由 **ResponseBuilder** 和 **CitationGenerator** 组装。

- **CitationGenerator**：从每个 RetrievalResult 生成 **Citation**（index、chunk_id、source、score、text_snippet、page 等），供引用 [1][2] 和结构化数据。
- **ResponseBuilder**：用 RetrievalResult 列表 + 可选 LLM 生成摘要，拼成 **Markdown 正文**（带 [1][2] 标记）+ **citations** 列表，得到 **MCPToolResponse**（content、citations、metadata、is_empty）。
- 若有图片等多模态内容，由 **MultimodalAssembler** 组装成 MCP 的 TextContent + ImageContent 块。

最终返回给调用方的是 content + structuredContent（含 citations），便于 AI 助手做来源标注。

---

### Q10b：Rerank 有哪几种后端？怎么选？

**答**：三种，由 **config/settings.yaml** 里 `rerank.provider` 决定。

1. **none**：**NoneReranker**，不精排，直接返回融合后的 top_k。
2. **cross_encoder**：**CrossEncoderReranker**，用 Cross-Encoder 模型对 (query, candidate) 打分排序，适合离线/低延迟精排。
3. **llm**：**LLMReranker**，用 LLM 对候选打分或排序，适合对语义要求高的场景。

**RerankerFactory** 根据 provider 创建对应实现；失败或超时时会回退到融合结果，不阻塞返回。

---

## 四、Evaluation 阶段

### Q11：什么是 Golden 用例？ground_truth 是什么？

**答**：

- **Golden 用例**：人工准备的测试用例，一条包含 query、可选的 expected_chunk_ids、expected_sources、reference_answer。用来衡量检索/答案是否和「标准」一致。
- **ground_truth**：在本项目里特指 **`{"ids": expected_chunk_ids}`**。EvalRunner 从 Golden 用例里取出 expected_chunk_ids 填进 ground_truth，传给 Evaluator。**CustomEvaluator** 用这份 ID 列表和实际检索到的 chunk_id 算 hit_rate、MRR；**RagasEvaluator** 不依赖 ground_truth，只用 query + 检索结果 + 生成答案做 LLM 打分。

---

### Q12：hit_rate 和 MRR 怎么算？依赖什么？

**答**：都依赖 **ground_truth 的期望 chunk ID 列表**和**实际检索到的 chunk_id 列表**。

- **hit_rate**：二值。若 retrieved_ids 里至少有一个在 ground_truth_ids 里则为 1，否则为 0。整份 test set 的 aggregate 就是「命中的 query 数 / 总 query 数」。
- **MRR（Mean Reciprocal Rank）**：对当前 query，在 retrieved_ids 里从前到后找第一个出现在 ground_truth_ids 里的位置 rank，得 1/rank；若没命中为 0。整份 test set 的 aggregate 就是所有 query 的 MRR 取平均。

需要 Golden 里填好 **expected_chunk_ids** 才有意义。

---

### Q13：report.aggregate_metrics 是什么？

**答**：评估报告里**所有 query 的指标取平均**后的结果，类型是 Dict[str, float]（指标名 → 平均值）。  
对每个指标 key，在所有「该 query 有该 key」的 QueryResult 上算算术平均，得到整份 Golden 测试集的总体表现（如平均 faithfulness、平均 hit_rate）。

---

### Q14：Ragas 和 Custom Evaluator 有什么区别？

**答**：

- **CustomEvaluator**：算 **hit_rate、MRR** 等 IR 指标，依赖 ground_truth 的 expected_chunk_ids，无外部依赖，适合回归和轻量评估。
- **RagasEvaluator**：用 **Ragas 框架**做 LLM-as-Judge，算 faithfulness、answer_relevancy、context_precision 等，**必须提供非空的 generated_answer**，不依赖 ground_truth IDs。需要安装 ragas、datasets。

**CompositeEvaluator** 可同时跑多个 Evaluator 并合并指标。

---

### Q14a：评估怎么跑？命令行和 Dashboard 分别怎么用？

**答**：**命令行**：**scripts/evaluate.py** 指定 Golden 测试集路径、top_k、collection，内部创建 EvalRunner + EvaluatorFactory.create(settings)，跑完输出 EvalReport（含每条 QueryResult 和 aggregate_metrics）。**Dashboard**：在 **Evaluation Panel** 页选择 evaluator（custom/ragas/composite）、Golden 文件、top_k，点击运行后同样调 EvalRunner，结果展示在页面，并可写入历史（如 JSONL）做对比。**Query Traces 页**还可对单条已存储的 query 点「Ragas 评估」，用该 query 重跑检索再调 RagasEvaluator，看单条指标。

---

## 五、概念与设计

### Q15：QueryProcessor 的 stopwords 和 FILTER_PATTERN 是干什么的？

**答**：

- **stopwords**：中英文停用词集合。分词后从关键词里去掉这些词，得到更干净的 keywords 给 Sparse（BM25）用。
- **FILTER_PATTERN**：正则 `(\w+):([^\s]+)`，用来从 query 里解析过滤语法（如 `collection:docs`），得到 filters 并从 query 里去掉这段再参与分词和关键词提取。

---

### Q16：为什么需要「融合后再按 metadata 过滤」？

**答**：底层存储对 metadata 过滤的支持不一致：向量库可能支持部分 filter，BM25 这边通常没有按 metadata 过滤。融合后的结果里可能混入不满足条件的 chunk，所以用 **metadata_filter_post** 在内存里再筛一遍，保证最终返回的列表都满足用户/query 的过滤条件。

---

### Q17：Ingestion 里为什么 Chunk 的 ID 要在 VectorUpserter 再生成一次？

**答**：DocumentChunker 用 doc_id（文档级）生成 ID，而**存储和检索**需要与「来源路径 + 块序号 + 内容」绑定的稳定 ID，且要兼容多文档、多来源。VectorUpserter 用 metadata.source_path、chunk_index 和 text 的 hash 生成，保证同一文件同一块在不同运行下 ID 一致，且与 BM25、向量库、评估里的 chunk_id 统一，便于追踪和去重。

---

### Q17a：向量库、LLM、Embedding 如何切换？需要改代码吗？

**答**：不用改代码，**抽象接口 + 工厂 + 配置文件**。

- **LLM**：BaseLLM 抽象，LLMFactory 按 `llm.provider` 创建（openai / azure / ollama / deepseek 等），配置在 settings.yaml 的 llm 段。
- **Embedding**：BaseEmbedding 抽象，EmbeddingFactory 按 `embedding.provider` 创建（openai / azure / ollama）。
- **VectorStore**：抽象接口，默认 **Chroma**（vector_store.provider: chroma），可扩展 qdrant、pinecone 等。
- **Reranker**：BaseReranker，RerankerFactory 按 `rerank.provider` 创建（none / cross_encoder / llm）。

改 settings.yaml 即可切换 provider 和模型，便于 A/B 或迁移。

---

### Q17b：可观测是怎么做的？Trace 存在哪？

**答**：**TraceContext** 贯穿单次请求，**TraceCollector** 持久化，**Streamlit Dashboard** 查看。

- **TraceContext**：每次 query 或 ingestion 创建一个，带 trace_id、trace_type（query/ingestion）、started_at；各阶段调用 **record_stage(stage_name, data, elapsed_ms)** 记录（如 query_processing、dense_retrieval、sparse_retrieval、fusion、rerank；或 load、split、transform、embed、upsert）。最后 **finish()** 写 finished_at。
- **TraceCollector**：收到 finish 后的 TraceContext，**append 一行 JSON** 到 `logs/traces.jsonl`（路径可配置），便于按行解析。
- **Dashboard**：Streamlit 应用读 traces.jsonl，提供 **Query Traces**（按 trace 看各阶段耗时与结果）、**Ingestion Traces**（每步 chunk 数、编码耗时等）、**Evaluation Panel**（跑 Golden、看 aggregate_metrics）、**Data Browser** 等。

这样从请求到存储到查看形成闭环，便于排查和监控。

---

### Q17c：配置和运行入口有哪些？

**答**：**配置**以 **config/settings.yaml** 为主：llm、embedding、vision_llm、vector_store、retrieval（dense_top_k、sparse_top_k、fusion_top_k、rrf_k）、rerank、evaluation、observability（trace_enabled、trace_file）、ingestion（chunk_size 等）。

**运行入口**：
- **MCP Server**：标准入口，暴露 query_knowledge_hub 等三个 Tool，被 Copilot/Claude 等调用。
- **脚本**：**scripts/query.py** 单次查询；**scripts/evaluate.py** 跑 Golden 评估。
- **Streamlit Dashboard**：启动后可做 ingestion 上传、查 Query/Ingestion Trace、跑评估、看数据浏览。

---

## 六、可观测与运维（补充）

### Q18：Query 一次请求会记哪些 Trace 阶段？

**答**：典型顺序：**query_processing**（QueryProcessor 产出 keywords、filters）→ **dense_retrieval**（向量检索）→ **sparse_retrieval**（BM25）→ **fusion**（RRF 融合）→ 若调用方做了 Rerank 则 **rerank**。每段记录输入/输出条数、耗时等，便于看瓶颈在哪一阶段。

---

### Q19：Dashboard 能做什么？

**答**：Streamlit 管理端主要功能：**Ingestion Manager**（上传文件、触发 pipeline、看进度）；**Query Traces**（按 trace_id 看某次查询的各阶段结果与耗时，可对单条做 Ragas 评估）；**Ingestion Traces**（看某次摄取的 load/split/transform/embed/upsert 明细）；**Evaluation Panel**（选 Golden 集、evaluator、top_k，跑 EvalRunner，看 aggregate_metrics 与历史）；**Data Browser**（按 source_hash 等查 chunk/图片）。Trace 数据来自 TraceCollector 写入的 traces.jsonl。

---

## 七、可口头简述的「项目亮点」

- **模块化**：Ingestion / Query / Evaluation 清晰分层，Loader、Chunker、Refiner、Evaluator 等可插拔、可配置。
- **双路检索 + RRF**：Dense 语义 + Sparse 关键词，并行检索后 RRF 融合，单路失败可降级。
- **可观测**：TraceContext 贯穿 pipeline，各阶段 record_stage；TraceCollector 落盘 traces.jsonl；Streamlit Dashboard 查 Query/Ingestion Trace 和评估报告。
- **评估闭环**：Golden 测试集 + Custom（hit_rate/MRR）+ Ragas（LLM-as-Judge），支持聚合指标与历史对比。
- **MCP 暴露**：通过三个 Tool 对外提供检索、列表、文档摘要，ResponseBuilder + CitationGenerator 返回带引用的结构化结果，便于 AI 助手做来源标注。
- **配置驱动、零代码切换**：LLM、Embedding、VectorStore、Reranker 均抽象 + 工厂，改 settings.yaml 即可切换 provider（OpenAI/Azure/DeepSeek/Ollama 等），便于 A/B 与迁移。

---

## 八、与简历对应的补充问题（防追问）

> 以下问题在简历中均有对应表述，面试官按简历深挖时可能问到，建议一并准备。

### Q20：简历里写的「幂等写入」具体怎么实现？

**答**：分两层。**（1）文件级**：完整性阶段用 **SQLiteIntegrityChecker**（或内存实现）存已处理文件的 file_hash；每次摄取前 `compute_sha256(file_path)` 得到 file_hash，`should_skip(file_hash)` 为真则整文件跳过；只有跑完全流程后才 `mark_success(file_hash, path, collection)`，失败不标记，下次可重试。**（2）Chunk 级**：VectorUpserter 用 **确定性 chunk_id**（source_hash + chunk_index + content_hash），同一文件同一块多次写入得到同一 ID；Chroma/BM25/ImageStorage 均按 id 做 upsert（如 Chroma 的 add 同 id 覆盖、SQLite INSERT OR REPLACE），因此重复跑同一文件不会重复堆积，只会覆盖更新。

---

### Q21：多 collection 在摄取和查询时分别怎么用？

**答**：**摄取**：`IngestionPipeline` 构造时传入 `collection`（如 `"contracts"`），该 pipeline 下所有写入（VectorUpserter、BM25Indexer、ImageStorage、integrity 的 mark_success）都带这个 collection；Chroma 用 collection_name 做命名空间，BM25 按 collection 存不同索引文件（如 `{collection}_bm25.json`），图片按 `data/images/{collection}/` 分目录。**查询**：调用 `HybridSearch.search(query, filters={"collection": "contracts"})` 或 MCP 工具里传 `collection` 参数，在融合后用 `_apply_metadata_filters` 只保留 metadata.collection 匹配的结果；**list_collections** 列出当前知识库下所有 collection 及文档数等。这样同一套服务可管理多套文档集合，互不干扰。

---

### Q22：双路检索（Dense / Sparse）有一路失败会怎样？

**答**：**单路失败**：若 Dense 或 Sparse 任一路抛错或超时，会**降级用另一路**的结果作为融合输入（另一路若为空则相当于只保留一路）；不会因为一路挂掉导致整次查询失败。**双路都失败**：则整次检索抛错，调用方（如 MCP 工具）可捕获后返回友好提示。这样在 BM25 未加载或向量库暂时不可用时仍能部分可用。

---

### Q23：完整性校验（Stage 1）用什么东西存？怎么判断跳过？

**答**：**存储**：默认用 **SQLiteIntegrityChecker**，背后是一张 SQLite 表（如 file_hash、file_path、collection、processed_at 等），路径可配置，支持 WAL、进程重启后持久。**判断跳过**：`compute_sha256(file_path)` 得到 file_hash → 调用 `should_skip(file_hash)`，内部查表看该 hash 是否已有成功记录，有则返回 True，pipeline 直接返回「已跳过」；否则继续后续阶段。只有 Stage 6 全部写完才 `mark_success(file_hash, path, collection)`，中间失败不标记，便于重跑。

---

### Q24：这个项目你负责哪几块？遇到的最大难点是什么？（个人项目可这样答）

**答**：（示例，可按实际调整）**负责**：独立完成整体架构设计与实现，包括 Ingestion 六阶段 pipeline、HybridSearch（QueryProcessor + 双路检索 + RRF + metadata 过滤）、MCP 三工具与 ResponseBuilder/CitationGenerator、Trace + Dashboard、Golden 评估与 Ragas 接入。**难点**：（1）**Chunk ID 两处生成**：要统一存储与检索、评估用的 ID，最终在 VectorUpserter 用 source_hash + chunk_index + content_hash 定稿，并和 DocumentChunker 的临时 ID 区分清楚；（2）**双路融合与 metadata 过滤**：向量库和 BM25 对 filter 支持不一致，采用「融合后再在内存里按 metadata 筛一遍」做兜底；（3）**可观测**：从 TraceContext、record_stage 到 TraceCollector 落盘、Dashboard 解析，保证各阶段数据可复现、便于排查。按简历上的「解决方案」和「成果」分点讲即可。

---

## 九、常见追问与参考答案

> 以下为各主问题回答后**面试官可能继续问**的追问，按主题归类；答主问题时可顺带想好对应话术。

### 架构与整体（对应 Q1、Q2、Q2a）

**追问 1-1**：为什么分 Ingestion / Query / Evaluation 三块，Evaluation 和 Query 能合并吗？  
**答**：职责分离：Ingestion 是离线写、Query 是在线读、Evaluation 是离线评测。合并的话会把「跑 Golden、打分、写报告」和「用户一次查询」混在一起，不利于配置和扩展；而且 Eval 需要重复跑检索、可能调不同 top_k，单独一块更清晰。

**追问 1-2**：为什么对外用 MCP 而不是直接提供 HTTP API？  
**答**：MCP 是协议标准，Copilot、Claude Desktop 等客户端原生支持，对接成本低；Tool 的入参出参有统一 schema，便于文档和调试。若业务还要给 Web/App 用，可以在 MCP Server 外再包一层 HTTP 网关。

**追问 1-3**：为什么用 Chroma 而不是 Milvus / Qdrant？  
**答**：项目规模适合单机、需要快速迭代，Chroma 开箱即用、无需单独部署，且项目里 VectorStore 是抽象接口，后续要换 Qdrant 只需实现接口并改配置即可。

**追问 1-4**：BM25 为什么自建而不是用 Elasticsearch？  
**答**：自建索引只存 chunk_id 和词频统计，依赖少、部署简单，和向量库同进程即可；ES 更重，适合已有 ES 或需要全文高亮的场景。当前 Sparse 只做召回，复杂度可控。

---

### Ingestion 阶段（对应 Q3–Q5a、Q20、Q23）

**追问 2-1**：六阶段顺序能换吗？比如先分块再加载？  
**答**：不能随意换。加载必须在前（没有 Document 就没有 Chunk）；完整性要在最前才能增量跳过；分块在变换前（变换针对 Chunk）；编码依赖最终 Chunk 内容；存储在最后。唯一可讨论的是「完整性是否必须第一步」——放前面可以最早跳过，省后续所有计算。

**追问 2-2**：Stage 5 编码时 Dense 和 Sparse 是串行还是并行？  
**答**：在 BatchProcessor 里对同一批 Chunk 先算 Dense（调 Embedding）、再算 Sparse（词频统计），当前是**串行**；两者互不依赖，理论上可以并行，但编码阶段通常 I/O 或 API 是瓶颈，并行能提升吞吐，具体看实现。

**追问 2-3**：DocumentChunker 的 ID 最后不用了，为什么还要生成？  
**答**：pipeline 里每个 Chunk 需要有唯一标识，方便 Trace、日志和后续阶段引用；且 Refiner/Enricher 等可能按 chunk.id 做去重或统计。最终写入存储时 VectorUpserter 会重新生成一套与「来源+序号+内容」绑定的 ID，两套在各自阶段都有用。

**追问 2-4**：ChunkRefiner 的「规则」具体做什么？  
**答**：规则主要是正则 + 字符串处理：去多余空白、常见页眉页脚、HTML 注释、无意义标记等，不调 LLM；做完规则后再可选调 LLM 做语义精炼。LLM 失败会回退到规则结果，不阻塞 pipeline。

**追问 2-5**：ImageCaptioner 失败会阻塞整条 pipeline 吗？  
**答**：不会。单张图 caption 失败会记日志、该图无 caption，其他 chunk 继续；整体设计是「可选增强、失败降级」，不因 Vision 调用失败导致摄取中断。

**追问 2-6**：文件内容改了但路径没变，会重新摄取吗？  
**答**：会。完整性看的是 **file_hash**（文件内容的 SHA256），内容一变 hash 就变，`should_skip(file_hash)` 为假，会重新走全流程并覆盖该文件对应的 chunk（因为 chunk_id 含 content_hash，会更新或新增）。

**追问 2-7**：mark_success 是在 Stage 6 哪一步调用的？  
**答**：在 Stage 6 **全部**写完（向量、BM25、图片、integrity 表）之后、pipeline 返回成功前调用。这样只有真正写成功的文件才会被标记，中途失败不会 mark_success，下次重跑会重新处理。

---

### Query 阶段（对应 Q6–Q10b、Q22）

**追问 3-1**：Dense 和 Sparse 各自取多少条？和最终 top_k 什么关系？  
**答**：配置里 **dense_top_k**、**sparse_top_k**（如各 20）是每路召回的条数；两路结果进 RRF 融合后按 RRF 分排序，再取 **fusion_top_k**（或调用方传的 top_k，如 10）作为最终结果。所以会「多召少取」，保证融合时有足够候选。

**追问 3-2**：为什么用 RRF 而不是按分数加权（如 0.7*dense_score + 0.3*sparse_score）？  
**答**：Dense 和 Sparse 的分数量纲和分布不同，直接加权要调权且对分数敏感；RRF 只依赖**排名**，对两路一视同仁，k 默认 60 是常用经验值，实现简单且稳定。若业务强需求加权，可以在融合层扩展另一种策略（如 weighted_sum），当前是 RRF。

**追问 3-3**：RRF 的 k=60 怎么定的？调过吗？  
**答**：60 是文献里常用默认值，能平滑排名差异。项目里用 config 的 `rrf_k`，可改；调大 k 会减弱高排名差异，调小会放大前几名的权重。若没有 A/B 数据，保持 60 即可。

**追问 3-4**：如果 _apply_metadata_filters 过滤后不足 top_k 条怎么办？  
**答**：保留过滤后的所有条（可能少于 top_k），不会补足；返回条数可能小于请求的 top_k，调用方可根据 result 数量判断。这样保证「凡返回的都满足 filter」，宁少勿滥。

**追问 3-5**：Rerank 超时或失败时具体怎么回退？  
**答**：CoreReranker 里对 Reranker 的 rerank() 做 try/except 或超时控制；一旦异常或超时，**不改原列表顺序**，直接按融合后的 RetrievalResult 顺序取前 top_k 返回，并在 trace/metadata 里标记未做 rerank，便于排查。

**追问 3-5a**：trace 传 None 会怎样？  
**答**：各阶段里会先判断 `if trace:` 再调用 `record_stage`；传 None 则不记录任何阶段，检索照常执行，只是没有 Trace 落盘，适合生产环境关可观测或单测里不关心 trace 的场景。

**追问 3-5b**：return_details 什么时候会设为 True？  
**答**：调试或需要看「Dense 单独结果、Sparse 单独结果、融合前列表」时；例如 Dashboard 展示各阶段召回条数、或排查某路异常。正常 MCP 调用一般用 False，只要最终 List[RetrievalResult]。

**追问 3-6**：query 里解析出 `collection:docs` 后，剩下的 query 文本还会参与检索吗？  
**答**：会。QueryProcessor 会把 `collection:docs` 从 query 里**去掉**，得到「纯净」的 query 文本再分词、提关键词；Dense 用**原句**（或去 filter 后的句子）做 embedding 检索，Sparse 用**关键词**检索。filter 只影响过滤，不减少检索语义。

**追问 3-7**：ResponseBuilder 不用 LLM 时，正文怎么来的？  
**答**：不调 LLM 时，正文一般是把 RetrievalResult 的 text 按顺序拼接成一段（或每段前加 [1][2] 标记），再交给 CitationGenerator 生成 citations；即「检索片段 + 引用标记」组成 content，没有摘要也可以返回。

**追问 3-8**：双路降级时，是只返回一路结果还是两路合并？  
**答**：**只返回成功的那一路**作为本次「融合」的输入。例如 Dense 失败就把 Sparse 结果当作唯一列表，按该列表的排名当作 RRF 的输入（相当于单路时 RRF 退化为原序），再截断 top_k。不会把「失败一路的空列表」和「成功一路」再合并。

---

### Evaluation 阶段（对应 Q11–Q14a）

**追问 4-1**：expected_chunk_ids 是谁提供的？怎么标？  
**答**：人工或半自动：对每条 Golden 的 query，人工判断「标准答案应该来自哪几个 chunk」，把这些 chunk 的 id（即 VectorUpserter 生成的那套）填进 expected_chunk_ids。可以用「先检索一次、人勾选正确片段、导出 id」的方式建库。

**追问 4-2**：一条 Golden 可以没有 expected_chunk_ids 吗？  
**答**：可以。没有的话 CustomEvaluator 的 hit_rate/MRR 无法算（或视为跳过）；RagasEvaluator 不依赖 expected_chunk_ids，只要有 query、检索结果和 generated_answer 就能算 faithfulness 等。

**追问 4-3**：MRR 里 rank 是 1-based 还是 0-based？  
**答**：**1-based**。第一个命中位置是 1 时，MRR=1/1=1；第一个命中在位置 2 时，MRR=1/2=0.5。这样和常见 IR 文献一致。

**追问 4-4**：Ragas 的 faithfulness 是什么意思？  
**答**：衡量「生成答案」是否忠实于「检索到的上下文」：是否无根据捏造、是否把上下文里的信息错误改写。一般由 Ragas 内部用 LLM 对 context + answer 打分得到。

**追问 4-5**：CompositeEvaluator 合并多个 Evaluator 时，指标名冲突怎么办？  
**答**：当前实现里各 Evaluator 返回的 metrics 的 key 不同（Custom 出 hit_rate、mrr，Ragas 出 faithfulness、answer_relevancy 等），一般不会冲突；若将来有重名，可以在合并时加前缀（如 custom_hit_rate、ragas_hit_rate）或约定命名空间。

---

### 概念与设计（对应 Q15–Q17c、Q17）

**追问 5-1**：stopwords 可以配置扩展吗？从哪加载？  
**答**：QueryProcessor 的 stopwords 默认是代码里写死的中英文集合；若配置里提供路径或列表，可以扩展为从文件/配置加载，当前实现是写死 DEFAULT_STOPWORDS。

**追问 5-2**：FILTER_PATTERN 的 value 里能带空格吗？  
**答**：正则是 `(\w+):([^\s]+)`，value 是「非空白」串，所以 **key 和 value 里都不能有空格**；若需要 `doc_type:technical doc` 这类，要改正则或约定用下划线等替代。

**追问 5-3**：为什么向量库本身支持 filter 还要做融合后 metadata 过滤？  
**答**：**兜底**：不同向量库对 filter 支持程度不一；且 **Sparse（BM25）这边通常没有按 metadata 过滤**，融合后的列表里可能混入其他 collection 的 chunk。在内存里再筛一遍能保证最终结果一定满足用户/query 的过滤条件。

**追问 5-4**：doc_id 和 source_path 有什么区别？存储为什么用 source_hash？  
**答**：doc_id 多是加载时生成的文档级 ID（如 UUID 或文件名）；source_path 是文件路径（如 `data/docs/a.pdf`）。存储用 **source_path 的 hash** 是为了：同一路径在不同机器上一致、且 ID 定长好存；用 path 而非 doc_id 能保证「同一文件」在不同次加载下得到同一 source_hash，便于去重和追踪。

**追问 5-5**：工厂里新加一个 provider（如百度文心）要改几处？  
**答**：实现一个符合 BaseLLM/BaseEmbedding 等接口的类 → 在对应 Factory 里 register 新 provider 名 → 在 settings 里加该 provider 的配置段。**调用链和主流程不用改**，符合开闭原则。

**追问 5-6**：traces.jsonl 会无限增长吗？有轮转或清理吗？  
**答**：当前是 **append 写、无自动轮转**；长期跑会变大。可以后续加：按日期切文件、或定期删 N 天前的行、或只保留最近 N 条；Dashboard 读时也可以按时间/ trace_id 过滤，避免一次全量加载。

**追问 5-7**：Dashboard 读 jsonl 是每次全量读还是按需分页？  
**答**：看实现；若文件不大可以全量读再在内存里分页展示；若文件很大，可以按行流式读、或只读最后 N 行、或建简单索引（如 trace_id → 文件偏移）按需读，避免 OOM。

---

### 与简历对应的追问（对应 Q20–Q24）

**追问 6-1**：Chroma 的 collection 和咱们说的 collection 是一对一吗？  
**答**：在本项目里是**一对一**：一个业务 collection（如 `contracts`）对应 Chroma 的一个 collection_name，对应 BM25 的一个索引文件、图片的一个子目录。Chroma 本身支持一个实例里多 collection，我们按业务 collection 名直接映射。

**追问 6-2**：如果重新做这个项目，你会先改哪一块？  
**答**：（示例）可能优先：**（1）Trace 轮转与查询**：jsonl 轮转 + 按条件查，避免文件过大；**（2）Sparse 与 Dense 编码并行**：缩短 ingestion 耗时；**（3）Golden 标注工具**：方便产出 expected_chunk_ids；**（4）更多融合策略**：如加权融合、可配置 RRF k 的 A/B。按自己真实体会说即可。

---

以上问题覆盖主问题、简历补充，以及**主问题回答后的常见追问**；面试时先答主问题，再根据面试官追问从「九、常见追问」里找对应点回答即可。
