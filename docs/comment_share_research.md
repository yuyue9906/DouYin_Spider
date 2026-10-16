# 抖音评论转发 API 逆向调查

调查时间：2026-10-16
目标页面：用户提供的抖音视频页面
目标动作：把视频下的一条评论，通过抖音 IM 转发给一个会话。

## 当前结论

用户要的是“评论转发 API”，不是 Playwright 点击脚本。静态代码已经把链路推进到 IM 消息发送层：评论被封装成 `SHARE_COMMENT` 消息，再交给 IM SDK 的 `SEND_MESSAGE` 处理器。旧版 SDK 静态代码将其路由到 `v1/message/send`，发送体为 `application/x-protobuf`。

截至 2026-10-16，已从本机项目凭据直接调用该 IM 接口，将两条测试评论各转发一次。两次响应均为 HTTP 200、`send_message_body.status=0 (SEND_SUCCEED)`、`check_code=0`。这验证了当前项目环境下的 Protobuf 构造、请求签名和评论卡片发送链路；会话凭据只从本机 `.env` 读取，没有保存到调查记录。

## 已确认的静态链路

1. 网页分享模块为评论构造 IM 消息，类型为 `SHARE_COMMENT`（SDK 枚举值 `105`）。评论 `content` 是 JSON 字符串，包含 `comment`、`comment_id`、评论作者 `secuid` 和昵称、作品 `itemId`、作者头像和作品封面信息，以及 `aweType` 等字段。
2. 选择会话后，网页调用 IM SDK 的 `sendMessageManager.createMessageBuilder(...).conversation(...).content(...).send()`。这证明真正的发送由 IM SDK 承担；`/aweme/v1/web/im_communication/share_report/` 是单独的 JSON 埋点/报告封装，现有代码没有证明它负责发送消息。
3. 同批旧版 IM SDK 包含 `SEND_MESSAGE` 的协议定义和实现：处理器 `forceHttp()` 返回 `true`，请求路径映射为 `v1/message/send`；SDK 将 `RequestBody` 编码为 protobuf，并通过配置的 `httpHost + path` 以 HTTP POST 发送，`Accept` 和 `Content-Type` 都是 `application/x-protobuf`。
4. `SendMessageRequestBody` 的已知字段包括会话 ID、会话短 ID/类型、消息内容、消息类型、客户端消息 ID、扩展字段、ticket、提及用户和引用消息信息。SDK 还会添加服务端时间偏移等扩展，并可能在发送前获取动态身份安全令牌及设备相关字段。

## 用户提供的实时抓包截图

2026-10-16 的 DevTools 截图已确认一次真实网络请求与静态 SDK 路径相符：目标主机为 `imapi.douyin.com`，路径为 `/v1/message/send`；请求方法是 `POST`，请求 `Content-Type` 为 `application/x-protobuf`，截图显示 HTTP 状态 `200`。另有 `OPTIONS` 预检请求，也返回 `200`，它不是实际消息发送。

截图中的响应体是 protobuf 二进制。按 SDK 字段解码后，内层 `send_message_body.status=0` 对应 `SEND_SUCCEED`，且 `check_code=0`；因此该次请求已被 IM 业务层接受。HTTP 200 单独只能说明 HTTP 响应成功，业务状态需要从 Protobuf 内层读取。

之后用户提供了一段从 protobuf 请求负载中复制出的内容，内含 `SHARE_COMMENT` 的评论 JSON，并与旧脚本结构一致：`aweType`、`comment_content_type`、`comment_id`、评论作者字段、封面/头像字段、`itemId`、`is_card`、`msgHint`。负载还带有 `s:client_message_id`、`s:stime` 和 IM SDK 扩展元数据，进一步确认实际分享走的是 IM 消息发送。由于文本存在二进制/字符集混杂，且其中包含动态身份令牌和会话鉴权材料，不把原文或任何令牌值保存到仓库。

**安全处理：**用户截图曾展示 Cookie、URL 令牌和会话/设备鉴权值；后续粘贴的 protobuf 负载又包含动态身份安全令牌。本文不记录这些值。每次重新登录后仍不要粘贴完整负载原文或请求头。继续验证时仅分享脱敏后的字段结构与成功/失败状态。

由此可知，接口形态不是“把评论 ID 传给独立的 share REST 接口”，而是经抖音 IM 的消息发送接口提交一条类型为 `105` 的 `SHARE_COMMENT` 消息。

## 项目直连 API 验证

- 新增 `ProtoBuilder.build_send_comment_share_request()`，按网页端消息类型 `105` 构造 Protobuf 请求，并生成客户端消息 ID、时间扩展和请求签名。
- 新增 `DouyinAPI.send_comment_share()`，构造 `aweType=10500` 评论卡片，调用 `https://imapi.douyin.com/v1/message/send`，并读取响应中的 `send_message_body.status`、`check_code` 和服务端消息 ID。
- 2026-10-16 通过本机 `.env` 凭据完成只读账号校验、评论检索和会话匹配；随后选取两条评论各发送一次。两条均返回 `SEND_SUCCEED`、`check_code=0`，没有自动重试。
- 返回状态证明 IM 服务端接受了发送请求；本次没有通过浏览器 UI 点击发送，也没有验证收件人是否已打开或阅读消息。

## Codex / 命令行入口

- `comment_share.forward_comment_share()` 是供其他 Python 代码或 Agent 调用的流程入口；`forward_comment_api.py` 是命令行包装。
- 必填定位信息为视频、评论（评论 ID 或完整文本）和收件人精确昵称。收件人昵称必须在搜索结果中唯一匹配；同名时要求用户补充 UID。回复评论另需一级父评论 ID。
- 命令默认只做查找与预览，不会创建私信会话或发送。只有显式指定 `--send` 才会创建/获取会话并发送一条评论卡片。
- 评论文本查找必须扫描完整页范围才能判定唯一；达到 `--max-pages` 上限仍有下一页时会停止，不会拿不完整结果发送。优先用评论 ID。
- 发送请求不自动重试；超时/响应不明时要求先检查私信，避免重复发送。CLI 输出不包含 Cookie、ticket、签名或私钥。
- 项目根目录 `AGENTS.md` 和 `skills/douyin-comment-forward/SKILL.md` 规定 Agent 缺视频、评论或收件人信息时先追问；如果用户已明确要求转发且目标唯一，无需额外重复确认。

## 当前抖音桌面客户端检查

用户提供的本机 Windows 抖音桌面客户端版本为 `8.4.0`。静态检查其 `resources/app.asar` 并抽取应用文件；`pages/app` 主要是 Electron 外壳，未找到 `SHARE_COMMENT`、`share_comment`、`sendMessageManager` 或 `createMessageBuilder` 等分享/IM 实现标记。

因此，目前没有证据表明逆向桌面客户端比网页端简单。网页端已有评论分享模块和对应 SDK 代码；桌面端本地包未暴露这段链路。就这个项目而言，先从网页端确认运行时 IM 请求更省力。桌面客户端静态代码可作为后续比对来源，但暂时不需要把它作为主路线。

## 证据来源

- 用户于 2026-10-16 提供的抖音网页截图：显示每条评论行独立的“分享”按钮。
- 本机留存的网页端静态脚本样本包含评论消息构造、会话选择后的 IM SDK 调用，以及 `v1/message/send` 路由和 Protobuf 序列化实现。样本属于抖音网页资源，未纳入本仓库。
- 旧网页脚本采样日期为 `2026-09-08`（见现有调查记录）；不能据此确认 2026-10-16 当前版本完全相同。
- 本机客户端包 `resources/app.asar` 的静态抽取结果：本地渲染应用目录未包含上述评论分享/IM SDK 字符串。该结论仅针对本地包体，不代表远端页面运行时不存在相关逻辑。

## 剩余边界

- 本次 `.env` 中的会话凭据通过账号校验并成功发送；尚未验证凭据过期后的自动刷新和长期可复用性。
- IM 服务端确认发送成功；收件人是否已看到或阅读，需要在私信中确认。
- REA 无法直接附加当前 Edge 标签页（`Debugger unattached`），但当前网页请求由用户提供的实时抓包确认，项目直连测试则由本地 API 响应验证。
