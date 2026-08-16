# 皮肤系统收尾 + 启动失败三重修复报告

> 日期：2026-08-17
> 背景：皮肤系统（另一会话按《皮肤系统重构计划》冻结版实施）Phase 1 提交后应用无法启动；
> 本次完成 Phase 1 收尾修复 + Phase 2 + Phase 3，并修复两个独立的启动阻塞。
> 验证：**全量 266 passed / 5 skipped（0 回归）**；真机全链路验收通过
> （bat → TTS 就绪 9880 → Agent 自动拉起 + 星空壁纸 + 零崩溃 + 嵌入模型离线秒载）。

---

## 一、"启动失败"的三个独立原因（全部已修复并验证）

| # | 根因 | 现象 | 修复 | 验证 |
|---|------|------|------|------|
| 1 | **bat 的 `if` 吞链**：`if not exist logs mkdir logs && python api_v2.py …` 中 `&&` 链整个成为 if 条件体，logs 已存在时 python 根本不执行 | TTS 永不启动，启动器空等 600s；TTS 日志 mtime 不变是铁证 | mkdir 守卫括号化 `(if not exist logs mkdir logs) & python …`（沙箱实证 logs 存在/不存在两场景） | bat 冷启动 → tts_api.log 被重写 → 9880 LISTENING → Agent 自动拉起 ✓ |
| 2 | **嵌入模型 HF 探测卡死**：`huggingface_hub 1.26` 的 `HF_HUB_OFFLINE` 在 **import 时快照**，先前把变量设在 import 之后 → 无效；模型明明在本地缓存，仍对 huggingface.co 发 HEAD ×5 重试（每次约 21s） | Agent 窗口白屏卡数分钟，日志刷 `WinError 10060 … Retrying` | 缓存命中（目录探测零网络）→ **import 前设** HF_HUB_OFFLINE + `local_files_only=True` 双保险，构造完恢复环境变量 | Agent 日志 HF 相关报错 **0 行**，"离线加载（跳过 HF 更新探测）"出现 ✓ |
| 3 | **皮肤 Phase 1 用了不存在的 API**：`ft.ImageFit.COVER` 在 Flet 0.86.5 中不存在（该版本枚举叫 `BoxFit`） | Agent 在 `_build_ui` 崩溃 `AttributeError: module 'flet' has no attribute 'ImageFit'`，窗口秒退 | 改为 `ft.BoxFit.COVER` | 真机启动 0 Traceback，窗口带壁纸正常拉起 ✓ |

## 二、皮肤系统按冻结计划逐项核对（全部 ✅）

| 计划条款 | 状态 | 证据 |
|----------|------|------|
| §3 六套皮肤（同源头像+壁纸，512²/≤500KB） | ✅ | starry/warm/winter/pale（Phase1）+ **sunset（本次，mockup 源图）+ sakura（本次，用户提供的樱花原图）** |
| §4 数据模型 + 无 DynamicColors + 一次性 apply_skin | ✅ | ui/design/skin.py；`ui/theme/dynamic.py` **未创建**（核对通过） |
| §5 SkinAvatarProvider + ImageAvatarProvider 兜底 | ✅ | avatar_provider.py 两层结构 |
| §6.1 PersonaDrawer「外观」区块（缩略图网格+跟随情绪开关） | ✅ | **本次 Phase 2 落地**；选中框即时反馈 + "重启后生效"提示 |
| §6.2 单一 `ui_skin` 字段（无 ui_skin_auto） | ✅ | config.py 仅 `ui_skin`；`.env.example` 仅 `UI_SKIN=auto` |
| §6.3 情绪联动 + 24h 过期 | ✅ | EMOTION_TO_SKIN + EMOTION_EXPIRY_SECONDS；情绪支柱（209da93）持久化的 `emotion`/`last_seen_at` 键与 skin.py 消费端**核对一致** |
| §6.4 红线：Skin 只属 UI 层，Agent 不知道 Skin | ✅ | `core/` 引用 ui/皮肤/主题的数量 = **0**；皮肤只读 agent_state（单向） |
| §7 皮肤初始化归 bootstrap，app.py 只消费 | ✅ | app.py 中 `load_skins/SkinManager` 引用 = 0，仅调 `bootstrap_skin()`（计划 §7 明确允许暂置于 ui/design/skin.py） |
| §8 文件清单（12 项） | ✅ | 全部落地，无缺项 |
| §9 测试（Phase1 4 套标准 + Phase3 6 套标准） | ✅ | `test_real_skins_on_disk_six_total` 等；皮肤测试 17 个 |
| §10 阶段 | ✅ | Phase1（+本次 BoxFit 收尾）/ Phase2 / Phase3 全部完成；**Phase4 为计划中"未来/可选"，不算缺口** |
| §11 资源规格 | ✅ | sunset 壁纸 45KB、sakura 壁纸 117KB、头像均 512² |

## 三、本次改动清单

- `ui/app.py`：ImageFit→BoxFit 崩溃修复；Drawer 接线（skins/ui_skin/current_skin_id/on_skin_selected）；`_on_skin_selected` 持久化回调
- `ui/components/persona_drawer.py`：「外观」区块（缩略图网格 + 跟随情绪开关 + 状态提示；未注入 skins 时隐藏，兼容旧测试）
- `ui/design/skin.py`：`SkinContext.skins` 字段（把注册表带给抽屉）
- `config.py`：`persist_ui_skin()`（只改 UI_SKIN 行，其余配置原样保留；env_path 参数供测试注入）
- `resources/skins/sunset/`、`resources/skins/sakura/`：同源 avatar+background+skin.json（sakura 粉白色板 / sunset 金橘色板，均 is_manual_only）
- `tests/test_skin_manager.py`：+6 测试（磁盘 6 套、persist 替换/追加、抽屉选肤/开关/隐藏、SkinContext.skins 透传）

## 四、验证记录

1. 全量 `pytest tests/ -q`：**266 passed, 5 skipped**（基线 260 → +6 新测试，0 回归）
2. 真机启动（新代码）：0 Traceback / 0 AttributeError，窗口正常拉起（星空壁纸），嵌入模型离线加载
3. **bat 全链路冷启动验收**：launcher 环境检查 → TTS 拉起（日志重写、约 4 分钟模型加载）→ 9880 LISTENING → Agent 自动启动（进程存活）→ 收尾横幅
4. 架构红线 grep 核对：core/ 零 UI 引用；dynamic.py 不存在；app.py 零皮肤扫描

## 五、遗留说明

- **换肤时机**：按计划冻结为"重启生效"（Phase 4 运行时预览未做，属计划内可选项）
- `tools/_skin_mockups/`（皮肤源图素材）保持不入库；`resources/skins/` 的产物已入库
- 皮肤选择的持久化写 `.env` 的 `UI_SKIN` 行；多实例同时写会以后写者为准（单机单实例场景无影响）

## 六、回滚

```bash
git log --oneline -3          # 本次提交 / 4f25d4b（启动修复）/ a446c66（皮肤Phase1）
git reset --hard 4f25d4b      # 回到皮肤收尾之前（保留启动修复）
git reset --hard a446c66      # 回到皮肤 Phase 1 原始状态（含 ImageFit 崩溃，不建议）
```
