# 快速测试指南

## 🎯 目的

验证V1.2质量感知摘要生成功能是否正常工作。

---

## ✅ 前置条件检查

### 1. 环境配置验证

运行集成测试：
```bash
docker exec ai-audio-assistant-web-api-1 python3 tests/test_v12_integration.py
```

**期望结果：** 显示 "🎉 所有测试通过！V1.2功能已准备就绪！"

### 2. 服务状态检查

```bash
docker-compose ps
```

**期望结果：** 所有服务状态为 "Up"

---

## 🧪 功能测试（需要测试音频）

### 场景1：基础摘要生成测试

如果你有5-10分钟的测试音频文件：

1. **获取JWT Token**（如果需要认证）
   ```bash
   # 登录获取token
   TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username":"YOUR_USERNAME","password":"YOUR_PASSWORD"}' \
     | jq -r '.data.access_token')
   ```

2. **上传测试任务**
   ```bash
   # 上传音频文件
   RESPONSE=$(curl -s -X POST http://localhost:8000/api/v1/tasks \
     -H "Authorization: Bearer $TOKEN" \
     -F "file=@your_test_audio.mp3" \
     -F 'options={"summary_style": "meeting"}')

   # 获取任务ID
   TASK_ID=$(echo $RESPONSE | jq -r '.data.id')
   echo "Task ID: $TASK_ID"
   ```

3. **监控任务进度**
   ```bash
   # 实时查看任务状态
   watch -n 2 "curl -s http://localhost:8000/api/v1/tasks/$TASK_ID \
     -H 'Authorization: Bearer $TOKEN' \
     | jq '.data | {status, progress, status_message}'"
   ```

4. **查看Worker日志**（另开终端）
   ```bash
   # 实时查看worker日志，关注质量评估和摘要生成过程
   docker logs -f ai-audio-assistant-web-worker-1 | grep -E "Quality|summary|chapter"
   ```

5. **等待任务完成后，获取摘要结果**
   ```bash
   # 获取所有摘要
   curl -s http://localhost:8000/api/v1/summaries/$TASK_ID \
     -H "Authorization: Bearer $TOKEN" \
     | jq '.data[] | {type: .summary_type, length: (.content | length)}'
   ```

6. **查看具体摘要内容**
   ```bash
   # 查看overview摘要
   curl -s http://localhost:8000/api/v1/summaries/$TASK_ID \
     -H "Authorization: Bearer $TOKEN" \
     | jq -r '.data[] | select(.summary_type=="overview") | .content'
   ```

### 验证要点

**✅ 应该看到：**
- 任务状态最终变为 `completed`
- 生成3个摘要：`overview`, `key_points`, `action_items`
- 摘要内容是结构化的Markdown格式
- Worker日志中显示质量评估信息

**🔍 日志关键字：**
```
Quality assessment: high/medium/low
Preprocessed text: XXX characters
Generated overview summary
Generated key_points summary
Generated action_items summary
Summary generation completed
```

---

## 🔍 无音频文件的验证方法

如果暂时没有测试音频，可以通过以下方式验证：

### 1. 检查数据库中的历史任务

```bash
# 进入PostgreSQL
docker exec -it ai-audio-assistant-web-postgres-1 psql -U audio_user -d audio_assistant

# 查询最近的任务
SELECT id, status, content_style, created_at
FROM tasks
ORDER BY created_at DESC
LIMIT 5;

# 查看某个任务的摘要
SELECT task_id, summary_type, LENGTH(content) as content_length
FROM summaries
WHERE task_id = 'YOUR_TASK_ID';
```

### 2. 查看Worker日志历史

```bash
# 查看最近的摘要生成日志
docker logs ai-audio-assistant-web-worker-1 --tail 200 | grep -A 10 "quality-aware summary"
```

### 3. 验证提示词模板

```bash
# 检查提示词模板文件
ls -lh app/prompts/templates/summary/
ls -lh app/prompts/templates/segmentation/

# 查看meeting风格的overview模板（部分）
cat app/prompts/templates/summary/zh-CN.json | jq '.prompts.overview.templates.meeting' | head -20
```

---

## 🎓 测试技巧

### 快速验证脚本

将以下内容保存为 `quick_check.sh`：

```bash
#!/bin/bash

echo "=== V1.2功能快速检查 ==="

echo ""
echo "1️⃣ 核心模块检查..."
docker exec ai-audio-assistant-web-api-1 python3 -c "
from app.utils.transcript_processor import TranscriptProcessor
from worker.tasks.summary_generator import generate_summaries_with_quality_awareness
print('✅ 核心模块导入成功')
" 2>&1 | grep -v Warning

echo ""
echo "2️⃣ LLM服务generate()方法检查..."
docker exec ai-audio-assistant-web-api-1 python3 -c "
from app.services.llm import DeepSeekLLMService, QwenLLMService
assert hasattr(DeepSeekLLMService, 'generate')
assert hasattr(QwenLLMService, 'generate')
print('✅ LLM服务generate()方法存在')
" 2>&1 | grep -v Warning

echo ""
echo "3️⃣ 提示词模板检查..."
docker exec ai-audio-assistant-web-api-1 python3 -c "
from app.prompts.manager import PromptManager
pm = PromptManager()
pm.get_prompt('summary', 'overview', 'zh-CN', {'transcript': 'test', 'content_style': 'meeting', 'quality_notice': ''})
pm.get_prompt('segmentation', 'segment', 'zh-CN', {'transcript': 'test', 'content_style': 'meeting', 'quality_notice': ''})
print('✅ 提示词模板加载成功')
" 2>&1 | grep -v Warning

echo ""
echo "4️⃣ 服务运行状态..."
docker-compose ps | grep -E "api-1|worker-1" | awk '{print $1, $6}'

echo ""
echo "✅ V1.2功能准备就绪！"
```

运行：
```bash
chmod +x quick_check.sh
./quick_check.sh
```

---

## 📊 预期性能指标

### 短内容（5-10分钟音频）

- **ASR时间：** 10-20秒
- **摘要时间：** 15-25秒
- **总时间：** 30-50秒
- **生成摘要：** 3个（overview, key_points, action_items）

### 长内容（30分钟+音频）

- **ASR时间：** 60-120秒
- **摘要时间：** 30-50秒
- **总时间：** 120-200秒
- **生成摘要：** 4个（含chapters）

---

## ❌ 常见问题

### 问题1：任务失败

**排查：**
```bash
# 查看任务错误信息
curl -s http://localhost:8000/api/v1/tasks/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.data.error_message'

# 查看Worker日志
docker logs ai-audio-assistant-web-worker-1 --tail 100 | grep -i error
```

### 问题2：生成的摘要为空

**排查：**
```bash
# 检查数据库
docker exec -it ai-audio-assistant-web-postgres-1 psql -U audio_user -d audio_assistant \
  -c "SELECT summary_type, LENGTH(content) FROM summaries WHERE task_id = '$TASK_ID';"

# 检查LLM服务配置
grep -E "^(DEEPSEEK|QWEN|DOUBAO)_API_KEY=" .env | sed 's/=.*/=***/'
```

### 问题3：Worker无响应

**排查：**
```bash
# 检查Celery worker状态
docker exec ai-audio-assistant-web-worker-1 celery -A worker.celery_app inspect active

# 重启Worker
docker-compose restart worker
```

---

## 📞 获取帮助

**相关文档：**
- 详细测试指南：`docs/V1.2_TESTING_GUIDE.md`
- 集成测试报告：`docs/V1.2_LOCAL_TEST_REPORT.md`
- 实施总结：`docs/V1.2_IMPLEMENTATION_SUMMARY.md`

**日志位置：**
- API日志：`docker logs ai-audio-assistant-web-api-1`
- Worker日志：`docker logs ai-audio-assistant-web-worker-1`

---

**版本：** 1.0
**创建日期：** 2026-01-16
