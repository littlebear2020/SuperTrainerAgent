# Canvas & Layout

Core canvas specifications and layout grids for Xiaohongshu infographics.

## Aspect Ratios

| Name | Ratio | 360 2K Pixels | Note |
|------|-------|---------------|------|
| square | 1:1 | 2048×2048 | Default for image cards |
| portrait-9-16 | 9:16 | 1152×2048 | Explicit vertical scenes |
| landscape | 16:9 | 2048×1152 | Landscape scenes |

**Default**: square. Use portrait-9-16 only when the user explicitly needs a vertical card; keep the prompt ratio and backend `--ar` value identical.

## Safe Zones

Avoid placing critical content in these areas:

| Zone | Position | Reason |
|------|----------|--------|
| bottom-overlay | Bottom 10% | Title bar overlay on mobile |
| top-right | Top-right corner | Like/share button overlay |
| bottom-right | Bottom-right corner | Watermark position |

```
┌─────────────────────────────┐
│                 [like/share]│  ← top-right: avoid
│                             │
│                             │
│      ✓ SAFE CONTENT AREA    │
│                             │
│                             │
│  [title bar overlay area]   │  ← bottom 10%: avoid key info
└─────────────────────────────┘
```

## Grid Layouts

### Density-Based Layouts

| Layout | Info Density | Whitespace | Points/Image | Best For |
|--------|--------------|------------|--------------|----------|
| sparse | Low | 60-70% | 1-2 | Covers, quotes, impactful statements |
| balanced | Medium | 40-50% | 3-4 | Standard content, tutorials |
| dense | High | 20-30% | 5-8 | Knowledge cards, cheat sheets |

### Structure-Based Layouts

| Layout | Structure | Items | Best For |
|--------|-----------|-------|----------|
| list | Vertical enumeration | 4-7 | Rankings, checklists, step guides |
| comparison | Left vs Right | 2 sections | Before/after, pros/cons |
| flow | Connected nodes | 3-6 steps | Processes, timelines, workflows |
| mindmap | Center radial | 4-8 branches | Concept maps, brainstorming, topic overview |
| quadrant | 4-section grid | 4 sections | SWOT analysis, priority matrix, classification |

## Layout by Position

| Position | Recommended Layout | Why |
|----------|-------------------|-----|
| Cover | sparse | Maximum visual impact, clear title |
| Setup | balanced | Context without overwhelming |
| Core | balanced/dense/list | Based on content density |
| Payoff | balanced/list | Clear takeaways |
| Ending | sparse | Clean CTA, memorable close |

## Grid Cells

For multi-element compositions:

| Name | Cells | Use Case |
|------|-------|----------|
| single | 1 | Hero image, maximum impact |
| dual | 2 | Before/after, comparison |
| triptych | 3 | Steps, process flow |
| quad | 4 | Product showcase |
| six-grid | 6 | Checklist, collection |
| nine-grid | 9 | Multi-image gallery |

## Visual Balance

### Sparse Layout
- Single focal point centered
- Breathing room on all sides
- Symmetrical composition

### Balanced Layout
- Top-weighted title
- Evenly distributed content below
- Clear visual hierarchy

### Dense Layout
- Organized grid structure
- Clear section boundaries
- Compact but readable spacing

### List Layout
- Left-aligned items
- Clear number/bullet hierarchy
- Consistent item format

### Comparison Layout
- Symmetrical left/right
- Clear visual contrast
- Divider between sections

### Flow Layout
- Directional flow (top→bottom or left→right)
- Connected nodes with arrows
- Clear progression indicators

### Mindmap Layout
- Central topic node
- Radial branches outward
- Hierarchical sub-branches
- Organic curved connections

### Quadrant Layout
- 4-section grid (2×2)
- Clear axis labels
- Each quadrant with distinct content
- Optional circular variant for cycles
