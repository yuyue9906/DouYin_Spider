# 公开性别 API 与主页图标调查（2026-10-17）

## 已观察到的证据

- 内置浏览器已创建目标主页标签，但页面连接两次超时；未完成内置浏览器中的接口读取。
- REA 的 `list_browser_targets` 返回 `endpoint_unreachable`（127.0.0.1:9222）。
- 随后使用 REA 临时、无登录状态的浏览器加载同一主页，仅等待网络加载，没有定位或读取图标元素。临时浏览器已由 REA 关闭。
- 捕获到 `GET https://www.douyin.com/aweme/v1/web/user/profile/other/`，HTTP 200。正文因并发读取上限未保留，不能宣称本次已读到有效性别。
- 网络证据：`ev_ed899dec019947ecabea0d3bed5db2600b59a9b02b408727f012a70817db6e4d`。存在事件丢弃和正文大小/并发限制，不能当作完整接口清单。
- 捕获到主页脚本地址：`https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/routes-User-id-route.88579c82.js`。
- 直接下载该公开脚本到 `output/gender-research/profile-route.js`，使用 REA 静态分析；父证据：`ev_52274b72c29903177a03179953353f9edd507a44efd12b513a89b3e974d844ed`。解析 1 个文件、48 个模块，无解析失败；语义图存在截断，不代表全部数据来源均已追踪。

## 图标兜底的误判风险

该脚本从组件 `userInfo` 中解构 `gender:E,age:L`，相关原始表达式为：

```js
("number"==typeof E&&0!==E||"number"==typeof L&&-1!==L)
0!==E&&(0,o.jsx)(F.Z,{src:1===E?t5:t4,width:12,height:12,viewBox:"0 0 12 12",style:{marginRight:"4px"}})
```

`t5` 是蓝色男性 SVG（fill `#168EF9`）；`t4` 是粉色女性 SVG（stroke `#F5588E`）。

将这些条件原样放到本地 Node 中求值，在年龄为 19 时：

| gender | 标签 | 图标分支 |
|---|---|---|
| 1 | 显示 | 男性 |
| 2 | 显示 | 女性 |
| 0 | 显示 | 无图标 |
| null | 显示 | 女性 |
| undefined | 显示 | 女性 |

这是代码条件的本地验证，不是对目标账号运行状态的完整复现。它说明：只要空值传入此组件且年龄有效，粉色图标就会被选中。因此，粉色图标本身不能证明用户公开了女性性别；上游是否对字段做变换，本次尚未完整追踪。

## API 取值规则

结合用户先前提供的公开资料响应，数字 1 对应男，数字 2 对应女。0、null、缺失或异常枚举在 API 阶段返回未知。

请求失败、登录失效、响应为空与“未公开”需要区分：这些应返回查询失败/待重试，不能直接标记为未公开。HTTP 200 本身不证明业务资料有效。

## 本次集成决策

用户选择保留图标颜色兜底，并接受已知误判风险。项目运行时先按 `user.gender` 判断；API 没有明确值时，Playwright 读取主页资料栏中观察到的蓝/粉 SVG 颜色，仍无结果则返回未知。年龄不参与推断，结果会标记来源和图标推断风险。实施与调用方法见 `docs/profile_gender_lookup.md`。

本次没有发现或验证独立的 RGB API。图标颜色读取仍需要无界面的 Playwright 页面；它不依赖逐个打开可见浏览器窗口。
