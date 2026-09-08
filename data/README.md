# data/ 测试数据目录

本目录**默认不纳入版本控制**（见根目录 `.gitignore`）。约定：**脚本只描述行为，
数据从 `data/` 读取**。

## 提交规范

- 仓库内只保留**脱敏示例**，放在 `data/examples/` 下，供脚本默认指向和新人跑通。
- 真实压测数据（生产域名 URL、真实账号、请求体等）请放在 `data/` 之外的本地文件
  或由 CI / 本地脚本生成，**严禁提交到仓库**，避免泄露公司域名与客户数据。

## examples 说明

| 文件 | 用途 | 对应脚本 |
| --- | --- | --- |
| `demo_health_payloads.csv` | 健康检查示例用例 | `locust_demo_health_baseline.py` |
| `pay_out_return_stress.json` | 支付回调示例（2 条脱敏请求体） | `run_pay_out_return_stress.py` |
| `uploader_authorizations_baseline.json` | 临时密钥接口示例（appKey 为占位符） | `run_uploader_authorizations_baseline.py` |
| `url_sample.txt` | 图片 URL 压测示例（example.com 占位） | `run_image_url_stress.py` |
| `taoche_browse_urls.csv` | 浏览场景权重 URL 示例（example.com 占位） | `run_taoche_browse_stress.py` |

真实场景请用各入口的 `--data-file` / `--url-file` / `--host` 参数覆盖默认值。