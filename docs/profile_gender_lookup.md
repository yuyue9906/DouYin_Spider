# 抖音公开性别查询

## 调用方式

单个主页可以直接调用：

```powershell
python scripts/query_public_gender.py "https://www.douyin.com/user/<sec_uid>"
```

默认先读取 `GET /aweme/v1/web/user/profile/other/` 的 `user.gender`。值为 `1` 返回男、值为 `2` 返回女；`0`、空值、缺字段或接口失败时，才启动一个无界面的 Playwright 页面，检查主页资料栏中的公开性别标记。

只查询 API、不启动浏览器时，添加 `--api-only`。批量评论采集可使用 `collect_ip_comments.py` 的 `--profile`；主页 API 未给出明确性别时默认执行相同兜底，也可以用 `--no-web-profile-fallback` 关闭。

## 兜底判定与结果

备用判断仅检查主页资料栏 SVG：蓝色 `fill=#168EF9` 视为男，粉色 `stroke=#F5588E` 视为女；没有命中颜色时，再检查资料栏中精确显示的“男”或“女”。不会用年龄、头像、昵称或评论文字推断。没有命中时返回“未知”，并在 `gender_source` 标明页面未显示或读取失败。

颜色规则按用户要求直接用于兜底。项目反编译到的主页脚本显示，空 gender 在有效年龄存在时也可能选择粉色图标，因此图标兜底可能把未公开性别误判为女。输出会通过 `gender_source` 和 `gender_confidence` 标记图标推断及其风险；需要严格避免误判时，应关闭浏览器兜底并只接受 API 的 1/2。

输出中的 `gender_code` 保留 API 原值，`gender` 是最终识别结果，`gender_public` 表示是否得到明确结果，`api_gender` 记录 API 阶段结果，`fallback_attempted` 表示是否运行备用方案。查询不读取或判断年龄。

## 登录要求

项目根目录 `.env` 中需要有已登录抖音网页端的 `DY_COOKIES`。Cookie 仅在本地构造 API 请求和 Playwright 浏览器会话时使用，不写入结果 JSON。
