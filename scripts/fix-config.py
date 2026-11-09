#!/usr/bin/env python3
"""Fix server config.yaml: restore glm-5.1 and glm-5-turbo, comment out deepseek-v4-pro."""
import re, subprocess

path = "/opt/trainer-ai/config.yaml"
with open(path, "r") as f:
    content = f.read()

glm51 = """  # 智谱 Coding Plan - GLM-5.1 (200K context, 128K output)
  - name: glm-5.1
    display_name: GLM-5.1 (智谱)
    use: deerflow.models.patched_openai:PatchedChatOpenAI
    model: GLM-5.1
    api_key: $ZHIPU_API_KEY
    base_url: https://open.bigmodel.cn/api/coding/paas/v4
    request_timeout: 600.0
    max_retries: 2
    max_tokens: 16384
    temperature: 0.7
    supports_vision: true
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled
    when_thinking_disabled:
      extra_body:
        thinking:
          type: disabled"""

glm5t = """  # 智谱 Coding Plan - GLM-5-Turbo (200K context, 128K output)
  - name: glm-5-turbo
    display_name: GLM-5-Turbo (智谱)
    use: deerflow.models.patched_openai:PatchedChatOpenAI
    model: GLM-5-Turbo
    api_key: $ZHIPU_API_KEY
    base_url: https://open.bigmodel.cn/api/coding/paas/v4
    request_timeout: 600.0
    max_retries: 2
    max_tokens: 16384
    temperature: 0.7
    supports_vision: true
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled
    when_thinking_disabled:
      extra_body:
        thinking:
          type: disabled"""

deepseek = """  # DeepSeek V4 Pro
  # [已禁用] 余额不足，需要时取消注释
  # - name: deepseek-v4-pro
  #   display_name: DeepSeek V4 Pro
  #   use: deerflow.models.patched_deepseek:PatchedChatDeepSeek
  #   model: deepseek-v4-pro
  #   api_key: $DEEPSEEK_API_KEY
  #   request_timeout: 600.0
  #   max_retries: 2
  #   max_tokens: 16384
  #   temperature: 0.7
  #   supports_vision: false
  #   supports_thinking: true
  #   when_thinking_enabled:
  #     extra_body:
  #       thinking:
  #         type: enabled
  #   when_thinking_disabled:
  #     extra_body:
  #       thinking:
  #         type: disabled"""

# Replace deepseek section
content = re.sub(
    r"  # DeepSeek V4 Pro.*?(?=\n  # 智谱|\n  - name: glm)",
    deepseek + "\n",
    content,
    flags=re.DOTALL,
)

# Replace glm-5.1 section
content = re.sub(
    r"  # 智谱 Coding Plan - GLM-5\.1.*?(?=\n  # 智谱 Coding Plan - GLM-5-Turbo)",
    glm51 + "\n\n",
    content,
    flags=re.DOTALL,
)

# Replace glm-5-turbo section
content = re.sub(
    r"  # 智谱 Coding Plan - GLM-5-Turbo.*?(?=\n  # 360|\n  - name: z-)",
    glm5t + "\n\n",
    content,
    flags=re.DOTALL,
)

with open(path, "w") as f:
    f.write(content)

print("config.yaml updated.")

# Validate
result = subprocess.run(
    ["python3", "-c", "import yaml; yaml.safe_load(open('/opt/trainer-ai/config.yaml')); print('YAML OK!')"],
    capture_output=True,
    text=True,
)
print(result.stdout.strip())
if result.returncode != 0:
    print("ERROR:", result.stderr)
