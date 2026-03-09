# 本项目 MCP Server 实现详解

按「启动 → 建 Server → 注册工具 → 收请求 → 执行工具」的顺序，说明项目里 MCP 是怎么跑起来的。

---

## 一、入口：进程怎么启动

**入口文件**：`src/mcp_server/server.py`

```
用户/客户端 执行:  python -m src.mcp_server.server
```

调用链：

```
main()
  → run_stdio_server()           # 同步包装
      → asyncio.run(run_stdio_server_async())
          → _redirect_all_loggers_to_stderr()   # 重要：日志不能打 stdout
          → create_mcp_server(...)              # 见下一节
          → mcp.server.stdio.stdio_server()     # 拿到 stdin/stdout 的流
          → server.run(read_stream, write_stream, ...)  # 进入 JSON-RPC 循环
```

**要点**：

- **stdout 只能给协议用**。所有 `logging` 被重定向到 **stderr**，否则会混进 JSON-RPC 报文，客户端解析失败。
- **stdio 传输**：客户端和 Server 是**同一个进程的父子**或通过管道相连；客户端往进程的 stdin 写请求，从 stdout 读响应。

---

## 二、Server 是怎么“造”出来的

`create_mcp_server(SERVER_NAME, SERVER_VERSION)` 在 `protocol_handler.py` 里，做了三件事。

### 2.1 创建“协议处理器”并注册所有工具

```text
create_mcp_server("modular-rag-mcp-server", "0.1.0")
  │
  ├─ protocol_handler = ProtocolHandler(server_name=..., server_version=...)
  │
  └─ _register_default_tools(protocol_handler)
        ├─ register_query_tool(protocol_handler)      # query_knowledge_hub
        ├─ register_list_tool(protocol_handler)       # list_collections
        └─ register_summary_tool(protocol_handler)     # get_document_summary
```

**ProtocolHandler** 是什么：

- 一个**工具注册表**：`tools: Dict[str, ToolDefinition]`。
- 每个 `ToolDefinition` 包含：`name`、`description`、`input_schema`（JSON Schema）、`handler`（async 函数）。

**每个工具怎么“挂”上去**（以 `query_knowledge_hub` 为例）：

在 `query_knowledge_hub.py` 里：

```python
def register_tool(protocol_handler) -> None:
    protocol_handler.register_tool(
        name=TOOL_NAME,                    # "query_knowledge_hub"
        description=TOOL_DESCRIPTION,       # 给 Agent 看的一长段说明
        input_schema=TOOL_INPUT_SCHEMA,    # {"type":"object", "properties": {...}}
        handler=query_knowledge_hub_handler # 真正干活的 async 函数
    )
```

所以：**“注册” = 在 ProtocolHandler 的字典里存一条「名字 → 定义」**，handler 的形参名要和 schema 里的 `properties` 对应，这样后面 `**arguments` 才能对上。

### 2.2 创建 SDK 的 low-level Server，并接上“列表/调用”

```python
# protocol_handler.py 里 create_mcp_server 后半段

server = Server(server_name)   # 官方 mcp.server.lowlevel.Server

@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    return protocol_handler.get_tool_schemas()

@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
    return await protocol_handler.execute_tool(name, arguments)

server._protocol_handler = protocol_handler
return server
```

含义：

- **Server**：只负责“按 MCP 协议收发包”，不负责“有哪些工具、怎么执行”。
- **list_tools**：客户端发 `tools/list` 时，SDK 会调你挂上去的 `handle_list_tools`，你直接交给 `protocol_handler.get_tool_schemas()`，从注册表里生成 `Tool(name, description, inputSchema)` 列表。
- **call_tool**：客户端发 `tools/call` 时，SDK 调 `handle_call_tool(name, arguments)`，你交给 `protocol_handler.execute_tool(name, arguments)`，由协议处理器查表并执行对应 handler。

所以：**“自建协议处理” = 你自己用 ProtocolHandler 维护“工具表 + 执行逻辑”，然后通过这两个装饰器把 MCP 的 tools/list 和 tools/call 接到这张表上。**

---

## 三、ProtocolHandler 内部：列表与执行

### 3.1 列表：get_tool_schemas()

客户端要“有哪些工具”时，会发 `tools/list`，最终走到：

```python
def get_tool_schemas(self) -> List[types.Tool]:
    return [
        types.Tool(
            name=tool.name,
            description=tool.description,
            inputSchema=tool.input_schema,
        )
        for tool in self.tools.values()
    ]
```

就是把注册表里每个 `ToolDefinition` 转成 MCP 规定的 `types.Tool`（name + description + inputSchema），没有传 handler，因为 handler 只在服务端用。

### 3.2 执行：execute_tool(name, arguments)

客户端发 `tools/call` 时，会带上工具名和参数字典，例如：

```json
{ "name": "query_knowledge_hub", "arguments": { "query": "如何配置 Azure？", "top_k": 5 } }
```

执行路径：

```python
# protocol_handler.py
tool = self.tools[name]                    # 按名字取出 ToolDefinition
result = await tool.handler(**arguments)   # 用 arguments 调用 handler
```

然后根据 handler 的返回值统一包成 MCP 的 `CallToolResult`：

- 已经是 `types.CallToolResult` → 直接返回；
- `str` → 包成一条 TextContent；
- `list`（content blocks）→ 包成 CallToolResult(content=..., isError=False)；
- 其他 → 转成 str 再包。

若 handler 抛异常：捉住后打成 `CallToolResult(..., isError=True)`，不把栈信息暴露给客户端。

所以：**每个工具的“协议层”逻辑（查表、调 handler、统一封装/错误）都在 ProtocolHandler 里；具体业务在各自 handler 里。**

---

## 四、一次 tools/call 的完整数据流（以 query_knowledge_hub 为例）

1. **客户端**（如 Cursor）发 JSON-RPC：  
   `method: "tools/call"`, `params: { name: "query_knowledge_hub", arguments: { query: "...", top_k: 5 } }`  
   → 写入进程的 **stdin**。

2. **SDK**（`server.run` 的循环）从 **read_stream**（stdin）读到这条请求，根据 method 找到你注册的 `handle_call_tool`，把 `name` 和 `arguments` 传进去。

3. **handle_call_tool** 调用  
   `await protocol_handler.execute_tool("query_knowledge_hub", {"query": "...", "top_k": 5})`。

4. **execute_tool** 在 `self.tools["query_knowledge_hub"]` 里取出 handler，执行  
   `await query_knowledge_hub_handler(query="...", top_k=5)`。

5. **query_knowledge_hub_handler**（在 `query_knowledge_hub.py`）里：
   - 通过 `get_tool_instance()` 拿到 `QueryKnowledgeHubTool` 单例；
   - `await tool.execute(query=..., top_k=..., collection=...)` 里做真正的 RAG：HybridSearch、Rerank、ResponseBuilder；
   - 返回 `types.CallToolResult(content=[...], isError=False)`（可能带 TextContent + ImageContent）。

6. **execute_tool** 拿到这个返回值，原样返回给 **handle_call_tool**。

7. **SDK** 把 `CallToolResult` 序列化成 JSON-RPC 响应，写入 **write_stream**（stdout）。

8. **客户端** 从 stdout 读到响应，解析出 content 给用户或 Agent。

---

## 五、各层职责小结

| 层级 | 位置 | 职责 |
|------|------|------|
| **入口** | `server.py` | 进程入口、日志重定向、创建 Server、stdio 循环 |
| **传输** | `mcp.server.stdio` | stdin/stdout 读写、JSON-RPC 编解码（SDK 内部） |
| **协议** | `mcp.server.lowlevel.Server` | 根据 method 分发到 list_tools / call_tool 等 |
| **自建协议处理** | `protocol_handler.py` | 工具注册表、get_tool_schemas、execute_tool、错误与返回格式统一 |
| **工具实现** | `tools/query_knowledge_hub.py` 等 | 每个工具的 schema + handler，内部再调 RAG（HybridSearch、Reranker 等） |

一句话：**SDK 管“怎么传、怎么解析/封装 JSON-RPC”；你的 ProtocolHandler 管“有哪些工具、怎么调、怎么包结果”；每个工具模块只管“我这条工具叫什么、要什么参数、怎么执行”。** 这样你就完全听懂了项目里“基于 low-level Server + stdio 自建协议处理”是怎么写的。
