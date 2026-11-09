# 画面设计规范与布局指南

## 基础参数

- **分辨率**: 1920×1080 (Full HD)
- **帧率**: 30fps
- **背景色**: `#f8f9fa`（浅灰白）或 `#ffffff`（纯白）
- **安全区域**: padding `50px 70px`（内边距）

## 配色方案

### 严肃通知风格（推荐）
```
主色(深蓝):  #1a3a5c   — 标题、强调
警示色(红):  #cc0000   — 禁止、警告、红线
背景色:      #f8f9fa   — 页面底色
卡片背景:    #ffffff   — 内容卡片
灰色文字:    #666666   — 次要信息
浅蓝背景:    #eef3f8   — 正面内容卡片
浅红背景:    #fff5f5   — 警示内容卡片
边框灰:      #e0e0e0   — 卡片边框
```

## 字体规范

| 元素 | 字号 | 字重 | 用途 |
|------|------|------|------|
| 主标题 | 48px | 800 | 场景标题 |
| 副标题 | 24px | 400 | 场景补充说明 |
| 正文 | 22-24px | 400-600 | 内容文字 |
| 强调数字 | 100px | 900 | 数据展示（250+、13.5万+） |
| 标签/徽章 | 20px | 600 | 分类标签 |
| 脚注 | 18-20px | 400 | 来源、说明 |

## 布局原则

### 元素居中集中
- 每个场景内容垂直水平居中
- 避免元素分散在画面四角
- 卡片最大宽度 ≤ 1100px，居中显示

### 多卡片布局
- **2个卡片**: flex-row 横排，各占 50%
- **3个卡片**: flex-row 横排，各占 33%
- **4个及以上**: 横排优先，放不下则 flex-wrap
- 卡片间距: `gap: 20px`

### Logo 位置
- 右上角: `position: absolute; top: 25px; right: 40px; height: 80px;`

## 动画规范

### 入场动画
- 淡入: `interpolate(frame, [0, 25], [0, 1])` — 通用
- 弹性缩放: `spring({ frame, fps, config: { damping: 100 } })` — 开场/结尾
- 交错入场: 每个元素延迟 20-35 帧

### 场景过渡
- 场景间无黑屏，直接切换
- 每场景结尾预留 30 帧（1秒）缓冲

## 通用组件

### SceneTitle（场景标题）
```tsx
const SceneTitle: React.FC<{ title: string; subtitle?: string; color?: string }> = 
  ({ title, subtitle, color = '#1a3a5c' }) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  return (
    <div style={{ opacity: op, marginBottom: 30, textAlign: 'center' }}>
      <div style={{ fontSize: 48, fontWeight: 800, color, letterSpacing: 2 }}>{title}</div>
      {subtitle && <div style={{ fontSize: 24, color: '#666', marginTop: 8 }}>{subtitle}</div>}
      <div style={{ width: 60, height: 3, backgroundColor: color, margin: '12px auto 0' }} />
    </div>
  );
};
```

### FadeIn（通用淡入）
```tsx
const FadeIn: React.FC<{ children: React.ReactNode; delay?: number; duration?: number }> = 
  ({ children, delay = 0, duration = 25 }) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [delay, delay + duration], [0, 1], { extrapolateRight: 'clamp' });
  return <div style={{ opacity: op }}>{children}</div>;
};
```

### BottomLine（底部装饰线）
```tsx
<div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 6, 
  background: 'linear-gradient(90deg, #cc0000, #1a3a5c)' }} />
```

## 常见场景模板

### 数据展示页
大数字居中 + 标签说明 + 底部来源

### 对比页（推荐 vs 禁止）
左右双卡片，左侧蓝/绿，右侧红

### 流程页
横向排列步骤，箭头连接

### 联系方式页
横排卡片 + 二维码图片
