# 图片输入参考

M14 之前，Nemo 的对话只能传文本。本参考描述当前代码实际支持的图片输入：上传、存储、随请求发送、历史保留，以及模型能力声明。图片生成不在范围内。

## 能力边界

- **只有输入**：模型可以看图，Nemo 没有图像生成或图像编辑工具，也没有图像输出通道；需要"出图"时只能由模型写代码或 `run_shell` 调用本机库完成。
- **能力标签由用户声明**：Provider/Model 的 `image_input` 是配置项，不校验上游。勾错时前端会放行附图，最终结果由上游决定。
- **视觉能力取决于模型**：同一 Provider 下不同 Model 的模态可能不同（例如 `deepseek-flash` 声明 `["text", "image"]`，而 `deepseek-v4-pro` 只有 `["text"]`）。

## 上传与读取 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions/{session_id}/images` | 请求体是图片原始字节，`Content-Type` 必须是 `image/png`、`image/jpeg` 或 `image/webp`；返回 `ImageRef` |
| POST | `/sessions/{session_id}/runs` | `images` 传 `asset_id` 列表，最多 8 个；`prompt` 与 `images` 至少有一个非空 |
| GET | `/sessions/{session_id}/images/{asset_id}` | 返回图片字节，`Cache-Control: no-store` 与 `X-Content-Type-Options: nosniff` |

`ImageRef` 字段：`asset_id`（内容 SHA-256）、`mime_type`、`size_bytes`、`width`、`height`。上传响应不回显图片内容。

## 限制

- 单张 ≤ 10 MiB，一次消息 ≤ 8 张，单次请求图片总量 ≤ 20 MiB，像素 ≤ 2000 万。
- 只接受 PNG、JPEG、WebP；动图被拒绝；声明格式必须与实际解码结果一致。
- 超限或非法图片以 `ToolFailure` 返回安全提示，不返回解码器原始错误。

## 存储

文件存放在 `~/.nemo/images/<sha256(session_id)>/<sha256(图片字节)>`，目录权限 `0700`，采用同目录临时文件加原子替换写入。读取时校验内容哈希，哈希不符返回完整性错误；路径必须落在存储根目录内，拒绝越界与符号链接逃逸。图片按 Session 隔离，跨 Session 读取被拒绝（`ToolFailure`）。

删除 Session 只清理数据库记录，磁盘上的图片目录当前不会被清理，属于已知遗留。

## 进入模型请求的方式

`openai_compatible` adapter 把带图消息编码为：一个 `text` 部分（正文为空时不发），后接按顺序排列的 `image_url` 部分，值是 `data:<mime>;base64,<内容>`，`detail` 为 `auto`。编码不修改会话历史；`image_input` 未声明或缺少会话图片存储时返回 `CapabilityError`。

编码前有两道能力检查：`RunManager.start` 在创建 Run 前检查会话与本次附图的模型能力，`ModelClient` 在每次请求前再检查一次。

## 历史、压缩与事件

图片引用保存在消息记录里，随工具调用轮次与上下文压缩一起保留；压缩只摘要文本，不改写原始消息与图片引用。事件、Trace 与 SSE 只包含图片元数据（`ImageRef`），不包含二进制内容。

## 界面行为

输入区支持点击、拖拽与粘贴添加图片，显示缩略图并可逐张移除；上传中禁止发送，切换 Session 时清空草稿与待发图片，发送失败时恢复文字与图片。历史消息里的图片按 Session 鉴权拉取，失败时显示"Image N unavailable"。当前模型未声明 `image_input` 时，前端在选文件阶段就拒绝并提示在 Connections 中启用。

## 相关文档

- 模型能力与 Provider 配置见 [配置参考](configuration.md)。
- Run 与 SSE 见 [Server API](server-api.md)。
- 交付记录见 [M14](../milestones/M14-image-input.md)。
