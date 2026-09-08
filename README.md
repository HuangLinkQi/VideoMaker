# VideoMaker

基于 agent 的导演工作台：剧本 → 资产 → 分镜拍摄方案 → 首尾帧 → 视频。页面审核，agent 执行。

## 运行

```bash
python3 studio/server.py          # 默认 http://127.0.0.1:8931
```

生成（GPU/TTS）受 `execution.mode` 控制。`paused` 时只允许审核和文字整理，不会自动提交生成。

## 技能

| 路径 | 用途 |
|---|---|
| `skills/video-studio/` | 流水线协议、事件消费、暂停与审核门 |
| `skills/minimax-h3-prompt-standardizer/` | MiniMax H3 提示词整理方法 |

使用时复制到项目 `.claude/skills/` 或用户技能目录。本仓库不包含登录凭证、`.venv` 和项目媒体文件。
