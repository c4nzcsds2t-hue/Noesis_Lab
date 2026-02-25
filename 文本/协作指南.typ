// --- 1. 全局格式设置 ---
#set text(
  font: ("Linux Libertine", "Source Han Serif"), 
  size: 10.5pt,                        
  lang: "zh"
)

// 设置页码
#set page(
  numbering: "1", 
  number-align: center
)

#set par(
  leading: 0.6em, 
  justify: true,       
)

#show heading: set text(font: "Source Han Sans", weight: "regular") 
#show heading.where(level: 1): it => [
  #set text(size: 14pt) 
  #v(0.5em)
  #it
  #v(0.5em)
]

// --- 2. 封面/标题部分 ---
#align(center)[
  #text(size: 16pt, weight: "bold")[Noesis Lab协作指南] \ \
  2026.02.25 | https://github.com/KiraYoshikage2021/Noesis_Lab
]

#v(1em)

= 1. Git的使用
