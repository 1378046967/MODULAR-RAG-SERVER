# Ingestion 阶段流程详解（结合 pipeline.py）

本文档按 **`src/ingestion/pipeline.py`** 中的 `IngestionPipeline.run(file_path, trace=None, on_progress=None)` 实际执行顺序，说明**框架、各阶段数据形态与变换**，并用与 query-flow 中 Rerank 相同的流程图风格给出整体流程。

---

## 一、框架与数据形态总览

| 阶段 | 输入数据 | 输出数据 | 主要组件 |
|------|----------|----------|----------|
| 1 完整性 | file_path (str) | file_hash；或跳过时 PipelineResult(skipped=True) | SQLiteIntegrityChecker |
| 2 加载 | file_path | Document(id, text, metadata) | Loader (e.g. PdfLoader/MarkitdownLoader) |
| 3 分块 | Document | List[Chunk]，每项 Chunk(id, text, metadata) | DocumentChunker → RecursiveCharacterTextSplitter |
| 4 变换 | List[Chunk] | List[Chunk]（原地增强 text/metadata） | ChunkRefiner → MetadataEnricher → ImageCaptioner |
| 5 编码 | List[Chunk] | dense_vectors, sparse_stats（BatchResult） | BatchProcessor → DenseEncoder + SparseEncoder |
| 6 存储 | chunks + dense_vectors + sparse_stats + document.metadata.images | vector_ids, BM25 索引, 图片索引 | VectorUpserter, BM25Indexer, ImageStorage |

**核心类型**（`src/core/types.py`）：

- **Document**：id, text, metadata（含 source_path, doc_type, images 等）
- **Chunk**：id, text, metadata（含 source_path, chunk_index, source_ref 等；变换后增加 title, summary, tags, refined_by, enriched_by, image_captions 等）
- **BatchResult**：dense_vectors（List[List[float]]）, sparse_stats（List[Dict]，每项含 chunk_id, term_frequencies, doc_length 等）

---

## 二、各阶段数据变换简述

- **Stage 1**：file_path → `compute_sha256` → file_hash；`should_skip(file_hash)` 为真则直接返回，否则进入 Stage 2。
- **Stage 2**：file_path → `loader.load(path)` → **Document**。text 为整文档正文（含 `[IMAGE: id]` 占位），metadata 含 source_path、doc_type、images 列表等。
- **Stage 3**：Document → `chunker.split_document(document)` → **List[Chunk]**。每个 Chunk.id 由 DocumentChunker 生成（格式 `{doc_id}_{index:04d}_{content_hash}`），metadata 继承文档并增加 chunk_index、source_ref。
- **Stage 4**：List[Chunk] 依次经 Refiner、Enricher、ImageCaptioner；**同一 List[Chunk]**，仅修改每项的 text 与 metadata（如 refined_by, title, summary, tags, image_captions）。
- **Stage 5**：List[Chunk] → `batch_processor.process(chunks)` → **BatchResult**：dense_vectors 与 chunks 一一对应；sparse_stats 与 chunks 一一对应，每项为 BM25 用统计（chunk_id, term_frequencies, doc_length）。
- **Stage 6**：chunks + dense_vectors → VectorUpserter 生成**最终 chunk_id**（格式 `{source_hash}_{chunk_index:04d}_{content_hash}`）并写入向量库，返回 **vector_ids**；sparse_stats → BM25Indexer.build → BM25 索引；document.metadata.images → ImageStorage.register_image。

---

## 三、Ingestion 整体流程（与 query-flow Rerank 同风格）

```
输入: file_path (Path/str), trace, on_progress, force
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: 完整性检查                                          │
│    file_hash = integrity_checker.compute_sha256(file_path)   │
│    · 若 not force 且 should_skip(file_hash) → 返回           │
│      PipelineResult(success=True, skipped=True)              │
│    · 否则 stages["integrity"] = { file_hash, skipped: False }│
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 2: 文档加载                                            │
│    document = loader.load(file_path)                         │
│    输出: Document(id, text, metadata)                        │
│    stages["loading"] = { doc_id, text_length, image_count }  │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 3: 分块                                                │
│    chunks = chunker.split_document(document)                 │
│    输出: List[Chunk]，Chunk.id = {doc_id}_{index:04d}_{hash} │
│    stages["chunking"] = { chunk_count, avg_chunk_size }      │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 4: 变换管道（同一 List[Chunk] 依次改写）                │
│    4a. chunks = chunk_refiner.transform(chunks, trace)       │
│        → text/metadata.refined_by                            │
│    4b. chunks = metadata_enricher.transform(chunks, trace)   │
│        → metadata.title, summary, tags, enriched_by          │
│    4c. chunks = image_captioner.transform(chunks, trace)     │
│        → metadata.image_captions（含图片段）                  │
│    stages["transform"] = { refiner, enricher, caption 统计 }  │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 5: 编码                                                │
│    batch_result = batch_processor.process(chunks, trace)     │
│    输出: BatchResult(dense_vectors, sparse_stats, ...)      │
│    · dense_vectors: List[List[float]]，与 chunks 一一对应   │
│    · sparse_stats: List[Dict]，每项 chunk_id, term_freqs...  │
│    stages["encoding"] = { dense_vector_count, sparse_doc_count } │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 6: 存储                                                │
│    6a. vector_ids = vector_upserter.upsert(chunks, dense_vectors, trace) │
│        · 内部用 source_path+chunk_index+text 生成最终 chunk_id │
│        · 写入向量库，返回 vector_ids (List[str])             │
│    6b. bm25_indexer.build(sparse_stats, collection, trace)  │
│    6c. 对 document.metadata.images 逐条 register_image      │
│    integrity_checker.mark_success(file_hash, file_path, collection) │
│    stages["storage"] = { vector_count, bm25_docs, images_indexed } │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 返回                                                         │
│    PipelineResult(success=True, file_path, doc_id,           │
│                   chunk_count, vector_ids, stages)           │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、各阶段流程与方法详解

以下按 Stage 1～6 分别说明每阶段的**流程步骤**、**调用的方法**及其作用、**输入/输出**与**代码位置**。

---

### Stage 1：文件完整性检查

**目的**：用文件 SHA256 判断是否已成功处理过，避免重复摄取；失败过的文件可重试。

**流程**：

```
输入: file_path (str), force (bool)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. compute_sha256(file_path)                                 │
│    · 按 64KB 分块读文件，计算 SHA256，返回 64 位十六进制字符串  │
│    · 文件不存在或非文件则抛 FileNotFoundError / IOError        │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 若 not force：should_skip(file_hash)                       │
│    · 查 SQLite ingestion_history 表，file_hash 对应 status    │
│    · 若 status == 'success' → 返回 True，pipeline 直接返回     │
│      PipelineResult(success=True, skipped=True)              │
│    · 否则返回 False，继续 Stage 2                             │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
  stages["integrity"] = { file_hash, skipped: False }
```

**方法说明**：

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `compute_sha256(file_path: str) -> str` | SQLiteIntegrityChecker | 计算文件内容 SHA256，64 字符十六进制 |
| `should_skip(file_hash: str) -> bool` | SQLiteIntegrityChecker | 查 DB，若该 hash 已 status='success' 则返回 True |

**代码位置**：`pipeline.py` L229–L247；`src/libs/loader/file_integrity.py`（SQLiteIntegrityChecker）。

---

### Stage 2：文档加载

**目的**：从磁盘读取文件并解析为统一结构 **Document**（正文 + 元数据 + 可选图片引用）。

**流程**：

```
输入: file_path (str)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ loader.load(file_path)                                       │
│    · PdfLoader：校验为 PDF，算 doc_hash，用 MarkItDown 解析   │
│    · 可选 _extract_and_process_images → 图片落盘，正文含     │
│      [IMAGE: id] 占位，metadata.images 列表                  │
│    · 返回 Document(id, text, metadata)                        │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
  stages["loading"] = { doc_id, text_length, image_count }
  trace.record_stage("load", {...}, elapsed_ms)
```

**方法说明**：

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `load(file_path: str \| Path) -> Document` | Loader（如 PdfLoader） | 解析文件为 Document；PdfLoader 内会 _validate_file、_compute_file_hash、MarkItDown.convert、_extract_title、_extract_and_process_images |

**输出**：Document 含 id（如 doc_{hash}）、text（Markdown，含 [IMAGE: id]）、metadata（source_path, doc_type, doc_hash, title, images 等）。

**代码位置**：`pipeline.py` L252–L279；`src/libs/loader/pdf_loader.py`（PdfLoader.load）。

---

### Stage 3：分块

**目的**：将 Document 的整段 text 切分为多个 **Chunk**，并为每个 Chunk 生成 ID、继承并扩展 metadata。

**流程**：

```
输入: document (Document)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ chunker.split_document(document)                              │
│    1. _splitter.split_text(document.text)                    │
│       → 得到 text_fragments: List[str]（如 RecursiveCharacterTextSplitter） │
│    2. 对每个 (index, text)：                                  │
│       · chunk_id = _generate_chunk_id(doc_id, index, text)   │
│         格式 {doc_id}_{index:04d}_{content_hash}，content_hash=text 的 SHA256 前 8 位 │
│       · chunk_metadata = _inherit_metadata(document, index, text) │
│         继承 document.metadata + chunk_index, source_ref, image_refs 等 │
│    3. 构造 Chunk(id=chunk_id, text=text, metadata=chunk_metadata) │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
  输出: List[Chunk]
  stages["chunking"] = { chunk_count, avg_chunk_size }
```

**方法说明**：

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `split_document(document: Document) -> List[Chunk]` | DocumentChunker | 编排：先 split_text，再逐段 _generate_chunk_id、_inherit_metadata，生成 Chunk 列表 |
| `_generate_chunk_id(doc_id, index, text) -> str` | DocumentChunker | 生成确定性 chunk ID：doc_id + 4 位序号 + text 的 8 位 hash |
| `_inherit_metadata(document, index, text) -> dict` | DocumentChunker | 从 document 继承 metadata，并加 chunk_index、source_ref、image_refs（从 [IMAGE: id] 解析） |

**代码位置**：`pipeline.py` L281–L314；`src/ingestion/chunking/document_chunker.py`。

---

### Stage 4：变换管道

**目的**：对同一批 Chunk 依次做**精炼正文**、**丰富元数据**、**图片描述**，不改列表长度，只改每项的 text 与 metadata。

**流程**：

```
输入: chunks (List[Chunk])
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4a. chunks = chunk_refiner.transform(chunks, trace)          │
│     · 对每个 chunk：先 _rule_based_refine(text)，再可选      │
│       _llm_refine(rule_refined_text)                         │
│     · 更新 chunk.text，metadata["refined_by"] = "rule"|"llm" │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4b. chunks = metadata_enricher.transform(chunks, trace)      │
│     · 对每个 chunk：先 _rule_based_enrich(text)，再可选      │
│       _llm_enrich(text) 得到 title/summary/tags 等            │
│     · 更新 chunk.metadata：title, summary, tags, enriched_by │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4c. chunks = image_captioner.transform(chunks, trace)         │
│     · 收集所有 chunk 中 [IMAGE: id] 涉及的图片，去重          │
│     · 对每张图调用 Vision LLM 生成 caption，写缓存            │
│     · 对含 [IMAGE: id] 的 chunk，在 metadata 中写入          │
│       image_captions（id → caption），可选把 caption 拼进 text │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
  输出: List[Chunk]（同一列表，内容已增强）
  stages["transform"] = { refiner, enricher, caption 统计 }
```

**方法说明**：

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `transform(chunks, trace) -> List[Chunk]` | ChunkRefiner | 规则 + 可选 LLM 精炼正文，写 refined_by |
| `transform(chunks, trace) -> List[Chunk]` | MetadataEnricher | 规则 + 可选 LLM 抽取 title/summary/tags，写 enriched_by |
| `transform(chunks, trace) -> List[Chunk]` | ImageCaptioner | 对 chunk 内引用的图片生成 caption，写 metadata.image_captions |

**代码位置**：`pipeline.py` L316–L374；`src/ingestion/transform/chunk_refiner.py`、`metadata_enricher.py`、`image_captioner.py`。

---

### Stage 5：编码

**目的**：为每个 Chunk 生成**稠密向量**和**稀疏统计**，供向量检索与 BM25 使用。

**流程**：

```
输入: chunks (List[Chunk])
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ batch_result = batch_processor.process(chunks, trace)        │
│    1. _create_batches(chunks) → 按 batch_size 分批           │
│    2. 对每个 batch：                                          │
│       · batch_dense = dense_encoder.encode(batch, trace)      │
│         → 调用 embedding_client.embed(texts)，得到 List[vec]  │
│       · batch_sparse = sparse_encoder.encode(batch, trace)   │
│         → 对每 chunk 统计 term_frequencies、doc_length，      │
│           输出 List[{ chunk_id, term_frequencies, doc_length }] │
│    3. 合并所有 batch 的 dense_vectors、sparse_stats           │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
  输出: BatchResult(dense_vectors, sparse_stats, batch_count, total_time, ...)
  stages["encoding"] = { dense_vector_count, dense_dimension, sparse_doc_count }
```

**方法说明**：

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `process(chunks, trace) -> BatchResult` | BatchProcessor | 分批调用 DenseEncoder 与 SparseEncoder，汇总 dense_vectors、sparse_stats |
| `encode(chunks, trace) -> List[List[float]]` | DenseEncoder | 用 embedding 客户端对 chunk.text 编码为向量 |
| `encode(chunks, trace) -> List[Dict]` | SparseEncoder | 对每 chunk 做分词与词频统计，输出 BM25 所需结构（chunk_id, term_frequencies, doc_length） |

**代码位置**：`pipeline.py` L376–L418；`src/ingestion/embedding/batch_processor.py`、`dense_encoder.py`、`sparse_encoder.py`。

---

### Stage 6：存储

**目的**：将向量写入向量库、用 sparse_stats 构建 BM25 索引、将文档内图片注册到图片索引，并在成功后标记完整性记录。

**流程**：

```
输入: chunks, dense_vectors, sparse_stats, document.metadata.images, file_hash, collection
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 6a. vector_ids = vector_upserter.upsert(chunks, dense_vectors, trace) │
│     · 对每个 (chunk, vector)：                                │
│       chunk_id = _generate_chunk_id(chunk)                   │
│         格式 {source_hash}_{chunk_index:04d}_{content_hash}   │
│         （source_hash 来自 metadata.source_path）             │
│     · 构造 record = { id: chunk_id, vector, metadata }       │
│       metadata 含原 chunk.metadata + text, chunk_id           │
│     · vector_store.upsert(records)                            │
│     · 返回 List[str] 即 chunk_id 列表（最终写入存储的 ID）     │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 6b. bm25_indexer.build(sparse_stats, collection, trace)       │
│     · 校验 term_stats 含 chunk_id, term_frequencies, doc_length │
│     · 计算语料统计：num_docs, avg_doc_length, 每 term 的 DF   │
│     · 对每个 term 算 IDF，建倒排 postings（chunk_id, tf, doc_length） │
│     · 持久化到 data/db/bm25/{collection}/                    │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 6c. 对 document.metadata.images 中每条：                      │
│     image_storage.register_image(image_id, file_path, collection, doc_hash, page_num) │
│     · 不拷贝文件，仅在 DB 中登记已有图片路径与元数据           │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ integrity_checker.mark_success(file_hash, file_path, collection) │
│     · 在 ingestion_history 表写入/更新 status='success'      │
│     · 供 Stage 1 should_skip 后续跳过                        │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
  stages["storage"] = { vector_count, bm25_docs, images_indexed }
  返回 PipelineResult(success=True, vector_ids=..., stages=...)
```

**方法说明**：

| 方法 | 所属类 | 作用 |
|------|--------|------|
| `upsert(chunks, vectors, trace) -> List[str]` | VectorUpserter | 按 chunk 生成最终 chunk_id、组 record，调用 vector_store.upsert，返回 chunk_id 列表 |
| `_generate_chunk_id(chunk) -> str` | VectorUpserter | 用 metadata.source_path、chunk_index、chunk.text 的 hash 生成存储用 ID |
| `build(term_stats, collection, trace)` | BM25Indexer | 从 sparse_stats 建倒排索引并持久化 |
| `register_image(image_id, file_path, collection, doc_hash, page_num)` | ImageStorage | 在索引中登记已存在的图片文件 |
| `mark_success(file_hash, file_path, collection)` | SQLiteIntegrityChecker | 记录该 file_hash 已成功处理 |

**代码位置**：`pipeline.py` L420–L530；`src/ingestion/storage/vector_upserter.py`、`bm25_indexer.py`、`image_storage.py`；`src/libs/loader/file_integrity.py`（mark_success）。

---

## 五、各阶段数据区别小结

| 阶段 | 输入类型 | 输出类型 | 关键字段/变化 |
|------|----------|----------|----------------|
| 1 | str (path) | file_hash / PipelineResult | 无 Document/Chunk |
| 2 | str (path) | Document | id, text, metadata.source_path, metadata.images |
| 3 | Document | List[Chunk] | Chunk.id 初版，metadata.chunk_index, source_ref |
| 4 | List[Chunk] | List[Chunk] | text 可能被 refine；metadata 增加 refined_by, title, summary, tags, enriched_by, image_captions |
| 5 | List[Chunk] | BatchResult | dense_vectors, sparse_stats，与 chunks 顺序一致 |
| 6 | chunks + dense_vectors + sparse_stats | vector_ids + 索引 | 最终 chunk_id 由 VectorUpserter 按 source_path+chunk_index+content 生成并写入存储 |

以上为 Ingestion 阶段框架、数据变换及与 query-flow 同风格的流程图说明。
