# generate_heatmap_chart — 热力图

## 功能概述
以颜色深浅展示两个分类维度交叉处的数值大小，适合「星期×时段」「地区×品类」等密度/强度对比场景。

## 输入字段
### 必填
- `data`: array<object>，每条记录包含 `x`（string，列维度）、`y`（string，行维度）与 `value`（number）。

### 可选
- `style.backgroundColor`: string，设置背景色。
- `theme`: string，默认 `default`，可选 `default`/`academy`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。
- `title`: string，默认空字符串。
- `axisXTitle`: string，默认空字符串。
- `axisYTitle`: string，默认空字符串。

## 使用建议
每个 (x, y) 组合至多一条记录，缺失组合按 0 渲染；每个维度的取值建议 ≤12，超出先做聚合。

## 返回结果
- 在 `--out` 指定路径生成 PNG 图片文件，可嵌入 docx/pdf 等文档。
