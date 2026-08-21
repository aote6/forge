# Forge 多模型免费接入说明

## 新增文件

把下面文件复制到你的仓库对应位置：

```
forge/adapters/openai_compat.py   # 通用 OpenAI 兼容适配器
forge/adapters/zhipu.py           # 智谱
forge/adapters/openrouter.py      # OpenRouter
forge/adapters/groq.py            # Groq

zp.py                             # 智谱入口（打 zp 启动）
or.py                             # OpenRouter 入口（打 or 启动）
gq.py                             # Groq 入口（打 gq 启动）
```

原来的 `dp.py`（DeepSeek）和 `gg.py`（Gemini）保持不变。

## 使用方式（和现在一样）

```bash
# 智谱免费主力
python3 zp.py

# OpenRouter 免费模型
python3 or.py

# Groq 高速
python3 gq.py

# 原来的
python3 dp.py   # DeepSeek 付费
python3 gg.py   # Gemini
```

如果你习惯直接打字母，可以在 `~/.bashrc` 或 Termux 的 `.bashrc` 里加：

```bash
alias zp='python3 /path/to/your/forge/zp.py'
alias or='python3 /path/to/your/forge/or.py'
alias gq='python3 /path/to/your/forge/gq.py'
alias dp='python3 /path/to/your/forge/dp.py'
alias gg='python3 /path/to/your/forge/gg.py'
```

然后 `source ~/.bashrc`，之后直接打 `zp` / `or` / `gq` 就能启动。

## 环境变量（注册完 key 后设置）

```bash
# 智谱（推荐先注册这个）
export ZHIPU_API_KEY=你的密钥
# 可选：换模型
# export ZHIPU_MODEL=glm-4.7-flash

# OpenRouter
export OPENROUTER_API_KEY=你的密钥
# 可选：换免费模型（带 :free 的）
# export OPENROUTER_MODEL=nvidia/nemotron-3-ultra:free

# Groq
export GROQ_API_KEY=你的密钥
# 可选
# export GROQ_MODEL=llama-3.3-70b-versatile

# 原来的
export DEEPSEEK_API_KEY=...
export GEMINI_API_KEY=...
```

建议写进 `~/.bashrc` 或 Termux 的启动脚本，避免每次重新 export。

## 注册顺序建议

1. **智谱**（国内最稳）  
   https://open.bigmodel.cn 或 https://bigmodel.cn  
   手机号注册 + 实名 → 创建 API Key  
   优先用 `glm-4.7-flash`（长期免费）

2. **OpenRouter**（国外最容易，谷歌账号即可）  
   https://openrouter.ai  
   谷歌登录 → Keys → Create Key  
   免费模型找带 `:free` 的

3. **Groq**（速度快）  
   https://console.groq.com  
   谷歌登录 → API Keys → Create

NVIDIA 如果卡在电话验证就先跳过。

## 切换模型示例

```bash
# 临时换智谱模型
ZHIPU_MODEL=glm-4.7-flash python3 zp.py

# 临时换 OpenRouter 模型
OPENROUTER_MODEL=google/gemma-4-31b-it:free python3 or.py
```

## 依赖

需要已安装 `openai` 包（你 DeepSeek 已经在用了）：

```bash
pip install openai
```

智谱 / OpenRouter / Groq 都走 OpenAI 兼容协议，不需要额外 SDK。
