# 基于 RAG + Memory Architecture 的角色型长期陪伴 Agent

助手长期陪伴型 AI Agent。本仓库为 **Phase 1：稳定人格（最小闭环）**。

> 设计原则：人格 / 知识 / 记忆三分离；先稳定人格 → 再记得我 → 最后像陪伴的人。
> 完整架构设计见对话中的计划文件（electric-pulse-curie.md）。

## 运行

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

cp .env.example .env         # 填入 DEEPSEEK_API_KEY
python main.py
```

## 目录（Phase 1）

```
config/persona/{identity,speech}.yaml   # 助手人格（改语气只动这里）
core/{llm_client,persona,prompt_builder,agent}.py
ui/app.py                                # Flet 桌面
config.py                                # .env 读取 + 缺 Key 校验
```

## 当前范围

- ✅ DeepSeek 流式对话、助手人格稳定、Flet 桌面 UI、短期记忆窗口
- ⏳ 长期记忆 / RAG / Emotion / 语音（后续 Phase 实现）
