const FEATURES = [
  { emoji: '🤖', title: '核心概念',
    desc: '为 Agent构建可靠环境，而非优化模型',
  },
  { emoji: '📊', title: '核心数据',
    desc: '3 名工程师 · 5 个月 → 100万行代码'
  },
  { emoji: '⚡️', title: '三大支柱',
    desc: '① 上下文工程 ② 架构约束 ③ 熵管理',
  },
  { emoji: '🔧', title: '核心实践',
    desc: '设计约束、系统'
  }
]
const tags = ["OpenAI", "AI工程", "Agent"]

const dark = themeDefinition = theme || defaultTheme

  const { width, height } = cardData.width
    const { height } = cardData.height
    const cw = width - pad * 2
    const ff = 'system-ui, -apple-system, sans-serif'

    const {
    const cardData = cardData
    const sections = cardData.sections || []
    const theme = resolveTheme(cardData.theme || defaultTheme
    const y = pad
    const cy = 20
    cy += section.titleFs + section.lh
    cy += headerHeight
    cy += pointsY = section.points.length * pointsGap
    cy += points.map((point, index) => {
      const pointY = cy + pointFs + pointLh
      cy += textH(point, maxW - pointFs, pointLh) + gap
    }
    
    // 最后一个section
    cy += sectionGap
    cy += metricsY = cardData.metrics ? flowMetrics(cardData.metrics, cw, tagH, tagGap).length : tags.length > 0 ? 0 : cy += tagsHeight
    else {
      cy += footerY = cardData.footer ? cy + 12 : cy + 10
      } else {
        cy += 15
      })
    })
    
    // 底部标签
    if (tags && tags.length > 0) {
      const tagW = textWidth(tag, tagFs) + 24
      const tags = flowTags(tags, cw, tagH, tagGap)
      tags.forEach((tag) => {
        const tagPillW = (
          <div
            key={i}
            style={{
              position: "absolute",
              left: 10
              bottom: 20,
              display: "flex",
              flexWrap: "wrap"
              gap: 8,
            }}
            <span
              x={10}
              y={tag.y + 20}
              fill={theme.text.accent}
              style={{
                fontSize: 14,
                fontWeight: "bold",
                fontFamily: ff
              }
              {section.title}
            </text>
            <text
              x={10}
              y={tagY}
              fill="rgba(255,255,255,0.88)"
              style={{
                fontSize: 13,
                fontFamily: ff
              }}
            >
              {point}
            </text>
          )
        })
      </g>
    )
    
    // 底部
    const footerY = totalHeight - 40
    const footerHeight = 40Data.footer ? 40 : lineHeight : 28 +  6 * 2 * 30(1, h + 30);
            y={height - 20 + 20} top={50}
            stroke="rgba(255,255,255,0.15)" strokeWidth={1}
            />
          </text>
          x={width - pad}
          y={footerY}
          textAnchor="middle"
          fill="rgba(255,255,255,0.4)"
          style={{
            fontSize: 12,
            fontFamily: ff
          }
        >
          {footer.text}
        </text>
        {footer.link && (
          <a href={footer.link} target="_blank" color={theme.text.accent} style={{ fontSize: 11}}>
            {footer.link}
          </text>
        )}
      </g>
    </div>
  );
}