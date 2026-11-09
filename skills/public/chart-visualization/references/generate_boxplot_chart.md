# generate_boxplot_chart — 箱型图

## 功能概述
展示各类别数据的分布范围（最值、四分位、异常值），用于质量监控、实验结果或群体分布比较。

## 输入字段
### 必填
- `data`: array<object>，每条记录包含 `category`（string）与 `value`（number）。`group`（string）字段本地渲染不支持——传入时按 `category` 分箱并输出 stderr 告警。

### 可选
- `style.backgroundColor`: string，设置背景色。
- `style.palette`: string[]，定义配色列表。
- `theme`: string，默认 `default`，可选 `default`/`academy`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。
- `title`: string，默认空字符串。
- `axisXTitle`: string，默认空字符串。
- `axisYTitle`: string，默认空字符串。

## 使用建议
单个类别至少提供 5 个样本以保证统计意义；如需展示多批次，可通过 `group` 或拆分多次调用。

## 返回结果
- 在 `--out` 指定路径生成 PNG 图片文件，可嵌入 docx/pdf 等文档。