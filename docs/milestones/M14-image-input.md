# M14 · 图片输入

## 1. 目标与范围

2026-10-13 完成对话图片输入：Session 内上传与存储、随请求发送给支持视觉的模型、历史与压缩保留、前端附件交互与历史图片展示。

范围只包含输入。图像生成、图像编辑工具与图像结果展示不在本里程碑，也未实现。编号说明：M13 已在 [M12](M12-mcp.md) 记录中合并使用（原 OAuth 交付），本交付改用 M14。

## 2. 交付物

- Core 图片契约：`ImageRef`、`ImageData`、`ImageStore` 端口与尺寸/张数/像素/字节上限常量。
- Filesystem 图片适配器：按 Session 分作用域、内容寻址、原子写入、读取校验与路径约束。
- Server 上传与读取 API、Run 请求的 `images` 参数、创建 Run 前的图片与能力校验。
- `openai_compatible` adapter 的多模态编码：文本 + 有序 `image_url` data URL。
- 前端附件输入（点击、拖拽、粘贴、移除、上传态）与历史消息图片展示。
- 图片在工具调用轮次与上下文压缩后的保留、依赖锁定与测试覆盖。
- [现行参考](../reference/images.md)。

## 3. 关键决策

图片按内容寻址并按 Session 隔离，存储路径由服务端生成，二进制不进入数据库、事件、Trace 或模型上下文以外的通道。会话历史只保存 `ImageRef` 元数据，请求时再从存储读取并编码，避免把大块 base64 写进持久层。

能力声明保持"配置即事实"：`image_input` 在 Provider/Model 设置中显式勾选，Runtime 在创建 Run 前与每次模型请求前各检查一次，缺声明直接返回 `CapabilityError`，不静默丢弃图片。前端在选文件阶段提前拦截，避免上传后才失败。

上传、单张、单请求与像素四项限额在契约层定义，适配器、Server 与前端共用同一组数值。图片被视为不可信输入：解码后校验格式与实际内容一致，拒绝动画与超大像素，读取时校验哈希与路径归属。

## 4. 实施摘要

先在 Core 定义契约与限额，再实现 Filesystem 存储与检查逻辑，接入 Server 的上传/读取端点与 Run 参数，最后在协议 adapter 完成多模态编码，前端补齐附件交互与历史渲染。

实施期间修正了两处判断：图片总量限额在 Run 创建前按"历史图片 + 本次图片"统一计算，避免只校验本次附图；`ImageRef` 比较必须同时匹配内容哈希与元数据，防止引用被替换。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -v`：388 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：50 项通过。
- `npm --prefix apps/ui run typecheck` 与 `npm --prefix apps/ui run build`：通过。
- macOS App 打包（`npm --prefix apps/ui run desktop:build`）：通过。
- 图片专项测试覆盖：存储重开与作用域、路径越界、完整性校验、非法/截断/动画与四项限额、编码顺序与历史不被改写、仅图片的 Run、历史保留与跨 Session 拒绝。

用户在真实会话中验收：`deepseek-flash` 携带图片的 Run 完成，模型正确识别图片内容。视觉能力另用两组探测确认（形状与颜色计数、图片内文字与小形状），并配同题纯文本对照以排除猜测；结果与端点 `/models` 声明的 `input_modalities` 一致。

## 6. 遗留

删除 Session 不清理由图片；`deepseek-v4-pro` 等纯文本模型仍可在 Connections 里勾选 `image_input` 而不被校验；图文混合的上下文占用未单独估算；图片没有缩略图缓存，历史图片每次进入会话都重新拉取。

图像生成与图像编辑工具未实现，需要时属于新的里程碑。上游是否接受多模态请求由 Provider 决定，Nemo 只保证编码与能力声明一致。
